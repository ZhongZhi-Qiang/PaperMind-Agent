"""L2 scene consolidation: organize L1 facts into themed scene blocks."""

from __future__ import annotations

import json
import logging
from typing import Any, Callable, Awaitable

from ..storage.l1_repo import L1Repo
from ..storage.l2_repo import L2Repo, L2Scene

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


class SceneExtractor:
    """Consolidates L1 facts into L2 scene blocks."""

    def __init__(self, l1_repo: L1Repo, l2_repo: L2Repo, llm_fn: LLMFn):
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
