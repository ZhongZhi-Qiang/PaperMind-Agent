"""Middleware: build memory context for injection into LLM prompts."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def build_recall_context(recall_result: dict[str, Any], max_chars: int = 3000) -> dict[str, str]:
    """Build formatted context strings from recall results.

    Returns:
        {
            "prepend_context": str,       # L1 facts to prepend to user message
            "append_system_context": str,  # L3 persona + L2 navigation to append to system prompt
        }
    """
    parts_prepend: list[str] = []
    parts_append: list[str] = []

    # L3 persona (stable, cacheable)
    persona = recall_result.get("l3_persona")
    if persona:
        parts_append.append(f"<user-persona>\n{persona}\n</user-persona>")

    # L2 scene navigation (index only, agent loads details via read_scene)
    scenes = recall_result.get("l2_scenes", [])
    if scenes:
        nav_lines = ["<scene-navigation>"]
        nav_lines.append("Scene index — use read_scene(scene_name) to load full content.")
        for s in scenes[:20]:  # Limit navigation entries
            nav_lines.append(f"- {s['scene_name']} ({s.get('fact_count', 0)} facts)")
        nav_lines.append("</scene-navigation>")
        parts_append.append("\n".join(nav_lines))

    # L1 facts (dynamic, changes per query)
    facts = recall_result.get("l1_facts", [])
    if facts:
        fact_lines = ["<relevant-memories>"]
        total = 0
        for f in facts:
            line = f"- [{f.get('fact_type', '?')}] {f.get('content', '')}"
            if total + len(line) > max_chars:
                break
            fact_lines.append(line)
            total += len(line)
        fact_lines.append("</relevant-memories>")
        parts_prepend.append("\n".join(fact_lines))

    return {
        "prepend_context": "\n\n".join(parts_prepend),
        "append_system_context": "\n\n".join(parts_append),
    }
