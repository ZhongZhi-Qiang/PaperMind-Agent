"""L2 scene consolidation: organize L1 facts into themed scene blocks."""

from __future__ import annotations

import json
import logging
from typing import Any, Callable, Awaitable

from ..storage.l1_repo import L1Repo
from ..storage.l2_file_repo import L2FileRepo, L2Scene

logger = logging.getLogger(__name__)

LLMFn = Callable[[str, str], Awaitable[str]]

CONSOLIDATE_SYSTEM = """You are a memory consolidation system. Your job is to organize atomic facts into coherent thematic scene blocks.

Each scene block should:
1. Cover a single topic/theme/scene
2. Have a descriptive name (in English, snake_case)
3. Contain related facts organized as a narrative
4. Include source references

Output JSON array of scene blocks:
```json
[
  {
    "scene_name": "descriptive_topic_name",
    "summary": "1-2 sentence summary of this scene",
    "fact_ids": [1, 2, 3],
    "content": "The scene narrative in markdown format"
  }
]
```

Rules:
- Group related facts together
- Don't duplicate facts across scenes
- Keep scenes focused (5-15 facts each)
- Use markdown formatting for readability"""

CONSOLIDATE_USER = """Here are the current L1 facts to organize:

{facts_text}

{existing_scenes_section}

Organize these facts into scene blocks. Return ONLY the JSON array."""

INCREMENTAL_SYSTEM = """You are a memory consolidation system. Your job is to merge new atomic facts into existing scene blocks.

Rules:
1. Assign each new fact to the most relevant existing scene, or create a new scene if it doesn't fit anywhere
2. If two existing scenes have become highly overlapping, merge them into one
3. If an existing scene has grown too large (>20 facts), consider splitting it
4. Update scene summaries to reflect the new facts
5. Only output scenes that were actually changed (new or modified)

Output JSON array of affected scene blocks:
```json
[
  {
    "scene_name": "existing_or_new_scene_name",
    "action": "update",
    "summary": "updated 1-2 sentence summary",
    "added_fact_ids": [51, 52],
    "content": "The full updated scene content in markdown",
    "note": "optional: reason for merge/split/create"
  }
]
```

action values:
- "update": merge new facts into an existing scene
- "create": create a brand new scene
- "merge": merge two+ scenes together (output one scene, note which names to remove)
- "split": split a scene into multiple (output multiple scenes with same original name)

CRITICAL: ONLY output scenes that changed. Unchanged scenes should NOT appear in the output."""

INCREMENTAL_USER = """Here are the existing scene blocks:

{existing_scenes_text}

New facts to merge (not yet assigned to any scene):

{new_facts_text}

Merge these new facts into the existing scenes. Output ONLY the scenes that need to change.
If a new fact fits in an existing scene, use action="update".
If a new fact is about a new topic, use action="create".
Return ONLY the JSON array."""


class SceneExtractor:
    """Consolidates L1 facts into L2 scene blocks."""

    def __init__(self, l1_repo: L1Repo, l2_repo: L2FileRepo, llm_fn: LLMFn):
        self._l1 = l1_repo
        self._l2 = l2_repo
        self._llm_fn = llm_fn

    async def consolidate(self) -> list[L2Scene]:
        """Run L2 consolidation: read all L1 facts, organize into scenes."""
        facts = self._l1.get_all(limit=1000)
        if not facts:
            logger.debug("No L1 facts to consolidate")
            return []

        # Format facts for LLM
        facts_lines = []
        for f in facts:
            facts_lines.append(
                f"- [id={f['fact_id']}] [{f['fact_type']}] {f['content']}"
            )
        facts_text = "\n".join(facts_lines)

        # Get existing scenes for context
        existing_scenes = self._l2.get_all()
        existing_section = ""
        if existing_scenes:
            scene_lines = [f"- {s['scene_name']}" for s in existing_scenes]
            existing_section = "Existing scenes (may need updating):\n" + "\n".join(scene_lines)

        user_prompt = CONSOLIDATE_USER.format(
            facts_text=facts_text,
            existing_scenes_section=existing_section,
        )

        try:
            raw = await self._llm_fn(CONSOLIDATE_SYSTEM, user_prompt)
        except Exception as exc:
            logger.error("L2 consolidation LLM call failed: %s", exc)
            return []

        scenes = self._parse_scenes(raw)
        if not scenes:
            logger.debug("No scenes generated")
            return []

        # Write scenes to DB
        saved = []
        for scene_data in scenes:
            scene = L2Scene(
                scene_name=scene_data.get("scene_name", ""),
                content_md=scene_data.get("content", ""),
                fact_ids=scene_data.get("fact_ids", []),
            )
            if scene.scene_name:
                self._l2.upsert(scene)
                saved.append(scene)

        logger.info("L2 consolidated %d facts into %d scenes", len(facts), len(saved))
        return saved

    async def consolidate_incremental(self, new_facts: list[dict[str, Any]]) -> list[L2Scene]:
        """Incremental L2: merge new facts into existing scenes.

        Only sends existing scene summaries + new facts to LLM,
        avoiding the cost of full rebuild every time.
        Falls back to full consolidation if no existing scenes.
        """
        if not new_facts:
            logger.debug("No new facts for incremental L2")
            return []

        # Get existing scenes with full content
        existing_scenes = self._l2.get_all_with_content()
        if not existing_scenes:
            # First run — do full consolidation
            return await self.consolidate()

        # Format new facts
        new_lines = []
        for f in new_facts:
            new_lines.append(
                f"- [id={f['fact_id']}] [{f.get('fact_type', '')}] {f['content']}"
            )
        new_facts_text = "\n".join(new_lines)

        # Format existing scenes as context
        scene_blocks = []
        for s in existing_scenes:
            scene_blocks.append(
                f"### {s['scene_name']}\n"
                f"**Summary**: {s.get('content_md', '')[:300]}\n"
                f"**Fact count**: {s.get('fact_count', 0)}"
            )
        existing_text = "\n\n".join(scene_blocks)

        user_prompt = INCREMENTAL_USER.format(
            existing_scenes_text=existing_text,
            new_facts_text=new_facts_text,
        )

        try:
            raw = await self._llm_fn(INCREMENTAL_SYSTEM, user_prompt)
        except Exception as exc:
            logger.error("Incremental L2 LLM call failed: %s", exc)
            return []

        scenes = self._parse_scenes(raw)
        if not scenes:
            logger.debug("No scene changes from incremental L2")
            return []

        # Apply changes
        saved = []
        names_to_delete: set[str] = set()
        for scene_data in scenes:
            action = scene_data.get("action", "update")
            scene_name = scene_data.get("scene_name", "")

            if action == "merge":
                # Merge: record old names to delete later
                old_names = scene_data.get("merged_from", [])
                if isinstance(old_names, list):
                    names_to_delete.update(old_names)

            if not scene_name:
                continue

            if action in ("update", "create", "merge"):
                # Read existing fact_ids for updates
                existing_ids = []
                if action in ("update", "merge"):
                    existing_content = self._l2.get_by_name(scene_name)
                    if existing_content:
                        # Extract fact_ids from frontmatter or previous state
                        for entry in self._l2.get_all():
                            if entry["scene_name"] == scene_name:
                                existing_ids = entry.get("fact_ids", [])
                                break

                added_ids = scene_data.get("added_fact_ids", [])
                all_ids = list(set(existing_ids + added_ids))

                scene = L2Scene(
                    scene_name=scene_name,
                    content_md=scene_data.get("content", ""),
                    fact_ids=all_ids,
                )
                self._l2.upsert(scene)
                saved.append(scene)

        # Clean up merged scene files
        for name in names_to_delete:
            if name not in {s.scene_name for s in saved}:
                self._l2.delete_by_name(name)

        logger.info(
            "Incremental L2: %d new facts → %d affected scenes (%d deleted)",
            len(new_facts), len(saved), len(names_to_delete),
        )
        return saved

    def _parse_scenes(self, raw: str) -> list[dict[str, Any]]:
        """Parse LLM response into scene data."""
        text = raw.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            text = "\n".join(lines)

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            start = text.find("[")
            end = text.rfind("]")
            if start >= 0 and end > start:
                try:
                    data = json.loads(text[start:end + 1])
                except json.JSONDecodeError:
                    logger.warning("Failed to parse L2 consolidation response")
                    return []
            else:
                return []

        return data if isinstance(data, list) else []
