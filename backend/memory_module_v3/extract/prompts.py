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

DEDUP_SYSTEM = """You are a memory deduplication system. Given a new memory and a list of existing similar memories, decide whether to merge or store.

Decisions:
- **merge**: The new memory is the SAME fact as an existing one (same meaning, possibly different wording or a small update). Merge it into the single best-matching existing memory and return that memory's fact_id.
- **store**: The new memory is genuinely new, or only superficially similar to the existing ones. Store it as a separate memory.

Rules:
1. Merge ONLY when the new memory and an existing memory refer to the same underlying fact
2. If the new memory adds meaningfully new information or concerns a different aspect, store
3. When in doubt, store — under-merging is safer than over-merging
4. If merging, pick exactly one target_fact_id (the best match)"""

DEDUP_USER = """New memory to evaluate:
"{new_memory}"

Type: {new_type}
Priority: {new_priority}

Existing similar memories:
{existing_memories}

Return JSON:
```json
{{
  "decision": "merge|store",
  "target_fact_id": null,
  "reason": "brief explanation"
}}
```
Return ONLY the JSON, no other text."""
