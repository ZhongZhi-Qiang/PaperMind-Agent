"""Pipeline scheduler: determines when L1/L2/L3 should trigger."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from ..config import MemoryV3Config
from .state import PipelineSessionState

logger = logging.getLogger(__name__)


class PipelineScheduler:
    """Evaluates trigger conditions for L1/L2/L3 pipeline stages."""

    def __init__(self, config: MemoryV3Config):
        self._cfg = config

    def should_run_l1(self, state: PipelineSessionState) -> bool:
        """Check if L1 extraction should run.

        Triggers:
        1. conversation_count >= warmup_threshold (exponential warmup)
        2. idle timeout: last L1 was > l1_idle_timeout_seconds ago
        """
        cfg = self._cfg

        # Warmup threshold check
        if state.conversation_count >= state.warmup_threshold:
            logger.debug(
                "L1 triggered by warmup threshold: count=%d >= threshold=%d",
                state.conversation_count, state.warmup_threshold,
            )
            return True

        # Idle timeout check
        if state.last_l1_at:
            idle_seconds = (datetime.now(timezone.utc) - state.last_l1_at).total_seconds()
            if idle_seconds >= cfg.l1_idle_timeout_seconds:
                logger.debug("L1 triggered by idle timeout: %.0fs >= %ds", idle_seconds, cfg.l1_idle_timeout_seconds)
                return True

        return False

    def advance_warmup(self, state: PipelineSessionState) -> None:
        """Advance warmup threshold after L1 runs: 1→2→4→8→...→pipeline_every_n."""
        cfg = self._cfg
        state.warmup_threshold = min(state.warmup_threshold * 2, cfg.pipeline_every_n)
        logger.debug("Warmup threshold advanced to %d", state.warmup_threshold)

    def should_run_l2(self, state: PipelineSessionState) -> bool:
        """Check if L2 scene consolidation should run.

        Triggers:
        1. pending_l2 flag set and delay after L1 elapsed
        2. max interval since last L2 exceeded
        """
        cfg = self._cfg

        if state.pending_l2 and state.last_l1_at:
            delay = timedelta(seconds=cfg.l2_delay_after_l1_seconds)
            if datetime.now(timezone.utc) >= state.last_l1_at + delay:
                logger.debug("L2 triggered by delay after L1")
                return True

        if state.last_l2_at:
            max_interval = timedelta(seconds=cfg.l2_max_interval_seconds)
            if datetime.now(timezone.utc) >= state.last_l2_at + max_interval:
                logger.debug("L2 triggered by max interval")
                return True

        # First run: no L2 ever
        if state.last_l2_at is None and state.last_l1_at is not None:
            delay = timedelta(seconds=cfg.l2_delay_after_l1_seconds)
            if datetime.now(timezone.utc) >= state.last_l1_at + delay:
                logger.debug("L2 triggered: first run after L1")
                return True

        return False

    def schedule_l2(self, state: PipelineSessionState) -> None:
        """Mark L2 as pending after L1 completes."""
        state.pending_l2 = True

    def should_run_l3(self, state: PipelineSessionState, total_facts: int) -> bool:
        """Check if L3 persona generation should run.

        Triggers when total facts reach l3_trigger_every_n, then re-triggers
        every l3_trigger_every_n new facts after the last L3 run.
        """
        cfg = self._cfg
        if total_facts < cfg.l3_trigger_every_n:
            return False
        # Enough new facts since last L3
        return total_facts - state.last_l3_fact_count >= cfg.l3_trigger_every_n
