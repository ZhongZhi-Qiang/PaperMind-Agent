"""Pipeline Manager: orchestrates L0→L1→L2→L3 lifecycle."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Callable, Awaitable

from ..config import MemoryV3Config, get_memory_v3_config
from ..storage.l0_file_repo import L0FileRepo
from ..storage.l1_repo import L1Repo
from ..storage.l2_repo import PipelineStateRepo
from ..storage.l2_file_repo import L2FileRepo
from ..storage.l3_file_repo import L3FileRepo
from ..extract.l1_extractor import L1Extractor
from ..extract.l1_dedup import L1Deduplicator
from .scheduler import PipelineScheduler
from .state import PipelineSessionState

logger = logging.getLogger(__name__)

EmbeddingFn = Callable[[str], Awaitable[list[float]]]
LLMFn = Callable[[str, str], Awaitable[str]]


class PipelineManager:
    """Manages the L0→L1→L2→L3 memory pipeline lifecycle.

    Called after each LLM turn:
    1. notify_conversation() — buffer message IDs, evaluate triggers
    2. _run_l1() — extract + dedup facts from buffered messages
    3. _run_l2() — consolidate L1 facts into scene blocks
    4. _run_l3() — generate user persona from scenes
    """

    def __init__(
        self,
        l0_repo: L0FileRepo,
        l1_repo: L1Repo,
        l2_repo: L2FileRepo,
        l3_repo: L3FileRepo,
        pipeline_repo: PipelineStateRepo,
        llm_fn: LLMFn,
        embedding_fn: EmbeddingFn,
        config: MemoryV3Config | None = None,
        on_change: Callable[[], None] | None = None,
    ):
        self._cfg = config or get_memory_v3_config()
        self._l0 = l0_repo
        self._l1 = l1_repo
        self._l2 = l2_repo
        self._l3 = l3_repo
        self._pipeline_repo = pipeline_repo
        self._on_change = on_change
        self._scheduler = PipelineScheduler(self._cfg)
        self._extractor = L1Extractor(l0_repo, l1_repo, llm_fn)
        self._dedup = L1Deduplicator(l1_repo, llm_fn)

        self._llm_fn = llm_fn
        self._embedding_fn = embedding_fn
        self._states: dict[str, PipelineSessionState] = {}
        self._l2_lock = asyncio.Lock()
        self._l3_lock = asyncio.Lock()

    def _get_state(self, session_id: str) -> PipelineSessionState:
        """Get or load pipeline state for a session."""
        if session_id in self._states:
            return self._states[session_id]

        # Load from DB
        db_state = self._pipeline_repo.get(session_id)
        if db_state:
            state = PipelineSessionState.from_dict(db_state)
        else:
            state = PipelineSessionState(session_id=session_id)

        self._states[session_id] = state
        return state

    def _save_state(self, state: PipelineSessionState) -> None:
        """Persist pipeline state to DB."""
        self._pipeline_repo.upsert(state.to_dict())

    async def notify_conversation(
        self,
        session_id: str,
        message_ids: list[int],
    ) -> None:
        """Called after each LLM turn. Evaluates pipeline triggers.

        Args:
            session_id: Current session
            message_ids: L0 message IDs from this turn (user + assistant)
        """
        state = self._get_state(session_id)
        state.conversation_count += 1
        state.buffered_message_ids.extend(message_ids)

        # Check L1 trigger
        if self._scheduler.should_run_l1(state):
            # Run L1 in background to not block the response
            buffered = list(state.buffered_message_ids)
            state.buffered_message_ids = []
            self._save_state(state)
            asyncio.create_task(self._run_l1_safe(session_id, buffered, state))
        else:
            self._save_state(state)

    async def _run_l1_safe(
        self,
        session_id: str,
        message_ids: list[int],
        state: PipelineSessionState,
    ) -> None:
        """L1 wrapper with error handling."""
        try:
            await self._run_l1(session_id, message_ids, state)
        except Exception as exc:
            logger.error("L1 pipeline failed for session %s: %s", session_id, exc)

    async def _run_l1(
        self,
        session_id: str,
        message_ids: list[int],
        state: PipelineSessionState,
    ) -> None:
        """L1 extraction: extract facts from buffered messages, dedup, write."""
        logger.info("Running L1 extraction for session %s (%d messages)", session_id, len(message_ids))

        # Extract
        facts = await self._extractor.extract(session_id, message_ids)
        if not facts:
            logger.debug("No facts extracted")
            state.last_l1_at = datetime.now(timezone.utc)
            self._scheduler.advance_warmup(state)
            self._save_state(state)
            return

        # Dedup against existing facts
        deduped = await self._dedup.dedup(facts)

        # Write facts (sync metadata, async embedding)
        for fact in deduped:
            if fact.fact_id:
                # Update existing
                self._l1.update(fact)
            else:
                # Insert new
                fact_id = self._l1.insert(fact)
                fact.fact_id = fact_id

        # Schedule async embedding for new facts
        asyncio.create_task(self._embed_l1_facts(deduped))

        # Update state
        state.last_l1_at = datetime.now(timezone.utc)
        self._scheduler.advance_warmup(state)
        self._scheduler.schedule_l2(state)
        self._save_state(state)

        logger.info("L1 complete: %d extracted, %d stored", len(facts), len(deduped))

        # Check L2 trigger
        if self._scheduler.should_run_l2(state):
            asyncio.create_task(self._run_l2_safe(session_id, state))

    async def _embed_l1_facts(self, facts: list) -> None:
        """Compute embeddings for L1 facts in background."""
        for fact in facts:
            if fact.fact_id and not fact.embedding:
                try:
                    embedding = await self._embedding_fn(fact.content)
                    self._l1.update_embedding(fact.fact_id, embedding)
                except Exception as exc:
                    logger.warning("L1 embedding failed for fact %d: %s", fact.fact_id, exc)

    async def _run_l2_safe(self, session_id: str, state: PipelineSessionState) -> None:
        """L2 wrapper with lock and error handling."""
        async with self._l2_lock:
            try:
                await self._run_l2(session_id, state)
            except Exception as exc:
                logger.error("L2 pipeline failed: %s", exc)

    async def _run_l2(self, session_id: str, state: PipelineSessionState) -> None:
        """L2 consolidation: merge new L1 facts into existing scene blocks.

        Incremental by default: only new facts since last L2 run are sent to LLM
        together with existing scene summaries. Falls back to full rebuild on first run.
        """
        from ..consolidate.scene_extractor import SceneExtractor
        extractor = SceneExtractor(self._l1, self._l2, self._llm_fn)

        # Query new facts since last L2 run
        if state.last_l2_at:
            new_facts = self._l1.get_since(state.last_l2_at)
        else:
            new_facts = []

        if new_facts:
            await extractor.consolidate_incremental(new_facts)
        else:
            logger.debug("No new facts since last L2, skipping consolidation")

        state.last_l2_at = datetime.now(timezone.utc)
        state.pending_l2 = False
        self._save_state(state)
        logger.info("L2 consolidation complete")

        # Signal cache invalidation
        if self._on_change:
            self._on_change()

        # Check L3 trigger
        total_facts = self._l1.count()
        if self._scheduler.should_run_l3(state, total_facts):
            asyncio.create_task(self._run_l3_safe(session_id, state, total_facts))

    async def _run_l3_safe(self, session_id: str, state: PipelineSessionState, total_facts: int) -> None:
        """L3 wrapper with lock and error handling."""
        async with self._l3_lock:
            try:
                await self._run_l3(state, total_facts)
            except Exception as exc:
                logger.error("L3 pipeline failed: %s", exc)

    async def _run_l3(self, state: PipelineSessionState, total_facts: int) -> None:
        """L3 persona generation."""
        from ..persona.persona_generator import PersonaGenerator
        generator = PersonaGenerator(self._l2, self._l3, self._llm_fn)
        await generator.generate()

        state.last_l3_at = datetime.now(timezone.utc)
        state.last_l3_fact_count = total_facts
        self._save_state(state)
        logger.info("L3 persona generation complete (total_facts=%d)", total_facts)

        # Signal cache invalidation
        if self._on_change:
            self._on_change()

    async def flush(self, session_id: str) -> None:
        """Force-run all pending pipeline stages for a session."""
        state = self._get_state(session_id)

        if state.buffered_message_ids:
            buffered = list(state.buffered_message_ids)
            state.buffered_message_ids = []
            await self._run_l1(session_id, buffered, state)

        if state.pending_l2:
            await self._run_l2(session_id, state)
