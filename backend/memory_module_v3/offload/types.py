"""Data types for the Symbolic Short-Term Memory pipeline.

Ported from TencentDB-Agent-Memory types.ts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any


def _now_china_iso() -> str:
    """Return current time in China Standard Time (CST, UTC+8) as ISO 8601."""
    tz = timezone(timedelta(hours=8))
    return datetime.now(tz).isoformat(timespec="seconds")


@dataclass
class ToolPair:
    """A buffered tool call + result pair waiting to be processed by L1.

    In-memory only, not persisted.
    """
    tool_name: str
    tool_call_id: str
    params: dict[str, Any] | str
    result: Any
    error: str | None = None
    timestamp: str = field(default_factory=_now_china_iso)
    duration_ms: int | None = None


@dataclass
class OffloadEntry:
    """A single offloaded tool call/result summary stored in offload JSONL."""
    timestamp: str
    node_id: str | None = None
    tool_call: str = ""
    summary: str = ""
    result_ref: str = ""
    tool_call_id: str = ""
    session_key: str | None = None
    score: int | None = None


@dataclass
class TaskJudgment:
    """L1.5 output: task lifecycle judgment."""
    task_completed: bool = False
    is_continuation: bool = False
    continuation_mmd_file: str | None = None
    new_task_label: str | None = None
    is_long_task: bool = False


@dataclass
class PluginState:
    """Persistent plugin state saved to state.json."""
    active_mmd_file: str | None = None
    active_mmd_id: str | None = None
    mmd_counter: int = 0
    last_session_key: str | None = None
    last_offloaded_tool_call_id: str | None = None
    last_l2_trigger_time: str | None = None


@dataclass
class MmdReplaceBlock:
    """A single replace block for incremental MMD updates."""
    start_line: int
    end_line: int
    content: str


@dataclass
class L2Response:
    """L2 LLM output: Mermaid generation/update."""
    file_action: str = "write"  # "write" or "replace"
    mmd_content: str | None = None
    replace_blocks: list[MmdReplaceBlock] = field(default_factory=list)
    node_mapping: dict[str, str] = field(default_factory=dict)


# Default configuration values (from TencentDB PLUGIN_DEFAULTS)
DEFAULTS = {
    "temperature": 0.2,
    "force_trigger_threshold": 4,
    "l1_size_threshold": 3000,
    "l2_null_threshold": 4,
    "l2_timeout_seconds": 300,
    "mild_offload_ratio": 0.5,
    "aggressive_compress_ratio": 0.85,
    "emergency_compress_ratio": 0.95,
    "emergency_target_ratio": 0.6,
    "max_pairs_per_batch": 20,
    "mmd_max_chars": 4000,
}
