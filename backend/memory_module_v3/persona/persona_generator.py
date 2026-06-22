"""L3 persona generation: synthesize user persona from L2 scene blocks."""

from __future__ import annotations

import logging
from typing import Any, Callable, Awaitable

from ..storage.l2_file_repo import L2FileRepo
from ..storage.l3_file_repo import L3FileRepo

logger = logging.getLogger(__name__)

LLMFn = Callable[[str, str], Awaitable[str]]

PERSONA_SYSTEM = """You are a persona synthesis system. Your job is to create a comprehensive user persona profile based on organized memory blocks.

The persona should include:
1. **Identity**: Who the user is (role, background, expertise)
2. **Preferences**: Communication style, technical preferences, workflow habits
3. **Goals**: Current objectives, projects, interests
4. **Context**: Ongoing work, recent activities, environment

Output a well-structured markdown document. Be specific and evidence-based — only include traits that are supported by the memory blocks.

At the end, add a "## Scene Navigation" section with links to each scene block."""

PERSONA_USER = """Here are the organized memory scene blocks:

{scenes_text}

Generate a comprehensive user persona profile in markdown format."""


class PersonaGenerator:
    """Generates L3 user persona from L2 scene blocks."""

    def __init__(self, l2_repo: L2FileRepo, l3_repo: L3FileRepo, llm_fn: LLMFn):
        self._l2 = l2_repo
        self._l3 = l3_repo
        self._llm_fn = llm_fn

    async def generate(self) -> str | None:
        """Generate user persona from all scene blocks."""
        scenes = self._l2.get_all()
        if not scenes:
            logger.debug("No scenes available for persona generation")
            return None

        # Format scenes for LLM
        scenes_lines = []
        for s in scenes:
            scenes_lines.append(f"## {s['scene_name']}\n{s['content_md']}\n")
        scenes_text = "\n---\n".join(scenes_lines)

        user_prompt = PERSONA_USER.format(scenes_text=scenes_text)

        try:
            persona = await self._llm_fn(PERSONA_SYSTEM, user_prompt)
        except Exception as exc:
            logger.error("Persona generation LLM call failed: %s", exc)
            return None

        # Add scene navigation links
        nav_lines = ["\n## Scene Navigation\n"]
        for s in scenes:
            nav_lines.append(f"- [[{s['scene_name']}]]")
        persona += "\n".join(nav_lines)

        # Save to file
        self._l3.set(persona)
        logger.info("L3 persona generated (%d chars)", len(persona))
        return persona
