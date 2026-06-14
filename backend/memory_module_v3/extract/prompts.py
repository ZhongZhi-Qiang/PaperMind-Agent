"""Prompts for L1 memory extraction and deduplication."""

from __future__ import annotations

# ---------------------------------------------------------------------------
# L1 Extraction: extract structured memories from conversation
# ---------------------------------------------------------------------------

EXTRACT_SYSTEM = """You are a memory extraction system. Your job is to extract structured memories from a conversation.

You will receive a conversation between a user and an assistant. Extract atomic, self-contained memories.

Memory types:
- **persona**: User's identity, preferences, traits, background. Example: "用户是数据科学家，偏好简洁回答"
- **episodic**: Events, activities, experiences. Example: "用户在2024-03-15部署了新服务到生产环境"
- **instruction**: Rules, instructions, guidelines given by the user. Example: "始终使用TypeScript strict模式"

Rules:
1. Each memory must be self-contained (understandable without context)
2. Keep memories concise (1-2 sentences max)
3. Do NOT duplicate obvious facts
4. Prefer specific facts over vague generalizations
5. Output valid JSON only"""

EXTRACT_USER = """Extract memories from this conversation.

{background_section}

--- NEW MESSAGES ---
{new_messages}
--- END ---

Return a JSON array of scene segments. Each segment groups related messages and their memories:

```json
[
  {{
    "scene_name": "descriptive_topic_name",
    "message_ids": [1, 2, 3],
    "memories": [
      {{
        "content": "The extracted memory in Chinese",
        "type": "persona|episodic|instruction",
        "priority": 0
      }}
    ]
  }}
]
```

If no meaningful memories can be extracted, return an empty array: []
Return ONLY the JSON, no other text."""

# ---------------------------------------------------------------------------
# L1 Dedup: decide how to handle new vs existing memories
# ---------------------------------------------------------------------------

DEDUP_SYSTEM = """You are a memory deduplication system. Given a new memory and a list of existing similar memories, decide what to do.

Decisions:
- **store**: The new memory is unique, store it as-is
- **update**: The new memory supersedes an existing one. Return which existing memory to replace and the updated content.
- **merge**: Combine new and existing into a single memory. Return the merged content.
- **skip**: The new memory is a duplicate, discard it.

Rules:
1. If the new memory adds no new information beyond what exists, skip it
2. If the new memory contradicts or updates an existing fact, update
3. If both contain unique partial information, merge
4. Be aggressive about deduplication — prefer skip/merge over store"""

DEDUP_USER = """New memory to evaluate:
"{new_memory}"

Type: {new_type}
Priority: {new_priority}

Existing similar memories:
{existing_memories}

Return JSON:
```json
{{
  "decision": "store|update|merge|skip",
  "target_fact_id": null,
  "reason": "brief explanation",
  "content": "final content (only if decision is update/merge)"
}}
```
Return ONLY the JSON, no other text."""
