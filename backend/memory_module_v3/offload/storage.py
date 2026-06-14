"""Storage layer for the Symbolic Short-Term Memory pipeline.

Handles JSONL offload entries, MMD files, state.json, and refs/*.md.
Simplified port from TencentDB-Agent-Memory storage.ts.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from .types import OffloadEntry, PluginState

logger = logging.getLogger(__name__)


class OffloadStorage:
    """File-based storage for the offload pipeline.

    Directory structure:
        <data_dir>/
        ├── state.json                  # Plugin persistent state
        ├── offload-<sessionId>.jsonl   # Structured summary index
        ├── refs/
        │   └── <timestamp>.md          # Full tool result originals
        └── mmds/
            └── <task-label>-<n>.mmd    # Mermaid symbolic diagrams
    """

    def __init__(self, data_dir: str | Path, session_id: str = "default"):
        self._data_dir = Path(data_dir)
        self._session_id = session_id
        self._refs_dir = self._data_dir / "refs"
        self._mmds_dir = self._data_dir / "mmds"
        self._offload_jsonl = self._data_dir / f"offload-{session_id}.jsonl"
        self._state_file = self._data_dir / "state.json"

    def ensure_dirs(self) -> None:
        """Create all required directories."""
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._refs_dir.mkdir(parents=True, exist_ok=True)
        self._mmds_dir.mkdir(parents=True, exist_ok=True)

    @property
    def data_dir(self) -> Path:
        return self._data_dir

    @property
    def refs_dir(self) -> Path:
        return self._refs_dir

    @property
    def mmds_dir(self) -> Path:
        return self._mmds_dir

    # ─── JSONL Operations ─────────────────────────────────────────────────

    def append_entries(self, entries: list[OffloadEntry]) -> None:
        """Append offload entries to the session JSONL with dedup."""
        if not entries:
            return

        self.ensure_dirs()

        # Dedup against existing entries
        existing_ids = set()
        if self._offload_jsonl.exists():
            try:
                for line in self._offload_jsonl.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        parsed = json.loads(line)
                        cid = parsed.get("tool_call_id", "")
                        if cid:
                            existing_ids.add(cid)
                            existing_ids.add(cid.replace("_", ""))
                    except json.JSONDecodeError:
                        continue
            except Exception:
                pass

        new_entries = []
        for e in entries:
            cid = e.tool_call_id
            if cid and (cid in existing_ids or cid.replace("_", "") in existing_ids):
                continue
            new_entries.append(e)

        if not new_entries:
            return

        lines = []
        for e in new_entries:
            obj = {
                "timestamp": e.timestamp,
                "node_id": e.node_id,
                "tool_call": e.tool_call,
                "summary": e.summary,
                "result_ref": e.result_ref,
                "tool_call_id": e.tool_call_id,
                "score": e.score,
            }
            if e.session_key:
                obj["session_key"] = e.session_key
            lines.append(json.dumps(obj, ensure_ascii=False))

        with open(self._offload_jsonl, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")

    def read_entries(self) -> list[OffloadEntry]:
        """Read all offload entries from the session JSONL."""
        if not self._offload_jsonl.exists():
            return []

        entries: list[OffloadEntry] = []
        try:
            for line in self._offload_jsonl.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    entries.append(OffloadEntry(
                        timestamp=obj.get("timestamp", ""),
                        node_id=obj.get("node_id"),
                        tool_call=obj.get("tool_call", ""),
                        summary=obj.get("summary", ""),
                        result_ref=obj.get("result_ref", ""),
                        tool_call_id=obj.get("tool_call_id", ""),
                        session_key=obj.get("session_key"),
                        score=obj.get("score"),
                    ))
                except json.JSONDecodeError:
                    continue
        except Exception as exc:
            logger.warning("Failed to read offload entries: %s", exc)

        return entries

    def update_node_ids(self, mapping: dict[str, str]) -> None:
        """Update node_id for entries by tool_call_id."""
        if not self._offload_jsonl.exists() or not mapping:
            return

        lines = self._offload_jsonl.read_text(encoding="utf-8").splitlines()
        changed = False
        new_lines = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                cid = obj.get("tool_call_id", "")
                if cid in mapping and obj.get("node_id") != mapping[cid]:
                    obj["node_id"] = mapping[cid]
                    changed = True
                new_lines.append(json.dumps(obj, ensure_ascii=False))
            except json.JSONDecodeError:
                new_lines.append(line)

        if changed:
            with open(self._offload_jsonl, "w", encoding="utf-8") as f:
                f.write("\n".join(new_lines) + "\n")

    # ─── Ref Operations ───────────────────────────────────────────────────

    def write_ref(self, ref_id: str, tool_name: str, content: str) -> str:
        """Store full tool result to refs/ directory. Returns relative path."""
        self.ensure_dirs()
        ref_path = self._refs_dir / f"{ref_id}.md"
        safe_content = content.replace("\x00", "")
        ref_path.write_text(
            f"# Tool Result: {tool_name}\n\n"
            f"**Ref ID**: `{ref_id}`\n\n"
            f"## Full Output\n\n```\n{safe_content}\n```\n",
            encoding="utf-8",
        )
        return f"refs/{ref_id}.md"

    def read_ref(self, ref_id: str) -> str | None:
        """Retrieve full result for a reference ID."""
        ref_path = self._refs_dir / f"{ref_id}.md"
        if not ref_path.exists():
            return None
        try:
            content = ref_path.read_text(encoding="utf-8")
            start = content.find("```\n")
            end = content.rfind("\n```")
            if start >= 0 and end > start:
                return content[start + 4:end]
            return content
        except Exception as exc:
            logger.warning("Failed to read ref %s: %s", ref_id, exc)
            return None

    # ─── MMD Operations ───────────────────────────────────────────────────

    def write_mmd(self, filename: str, content: str) -> None:
        """Write/overwrite an MMD file."""
        self.ensure_dirs()
        mmd_path = self._mmds_dir / filename
        mmd_path.write_text(content, encoding="utf-8")

    def read_mmd(self, filename: str) -> str | None:
        """Read an MMD file."""
        mmd_path = self._mmds_dir / filename
        if not mmd_path.exists():
            return None
        try:
            return mmd_path.read_text(encoding="utf-8")
        except Exception as exc:
            logger.warning("Failed to read MMD %s: %s", filename, exc)
            return None

    def list_mmds(self) -> list[str]:
        """List all MMD files."""
        if not self._mmds_dir.exists():
            return []
        return sorted(f.name for f in self._mmds_dir.iterdir() if f.suffix == ".mmd")

    def delete_mmd(self, filename: str) -> bool:
        """Delete an MMD file."""
        mmd_path = self._mmds_dir / filename
        if not mmd_path.exists():
            return False
        mmd_path.unlink()
        return True

    def patch_mmd(self, filename: str, blocks: list[dict[str, Any]]) -> bool:
        """Apply incremental line-based replace blocks to an MMD file.

        Each block: {start_line: int, end_line: int, content: str}
        Lines are 1-indexed.
        """
        original = self.read_mmd(filename)
        if original is None:
            return False

        lines = original.split("\n")
        sorted_blocks = sorted(blocks, key=lambda b: b["start_line"], reverse=True)

        for block in sorted_blocks:
            start = block["start_line"]
            end = block["end_line"]
            new_content = block.get("content", "")
            new_lines = new_content.split("\n") if new_content else []

            if start < 1 or start > len(lines) + 1:
                continue
            if end < start:
                lines.insert(start - 1, *new_lines)
            else:
                clamped_end = min(end, len(lines))
                delete_count = clamped_end - start + 1
                lines[start - 1:start - 1 + delete_count] = new_lines

        new_content = "\n".join(lines)
        if new_content != original:
            self.write_mmd(filename, new_content)
        return True

    # ─── State Operations ─────────────────────────────────────────────────

    def read_state(self) -> PluginState:
        """Read the state.json file."""
        if not self._state_file.exists():
            return PluginState()
        try:
            obj = json.loads(self._state_file.read_text(encoding="utf-8"))
            return PluginState(
                active_mmd_file=obj.get("active_mmd_file"),
                active_mmd_id=obj.get("active_mmd_id"),
                mmd_counter=obj.get("mmd_counter", 0),
                last_session_key=obj.get("last_session_key"),
                last_offloaded_tool_call_id=obj.get("last_offloaded_tool_call_id"),
                last_l2_trigger_time=obj.get("last_l2_trigger_time"),
            )
        except Exception:
            return PluginState()

    def write_state(self, state: PluginState) -> None:
        """Write the state.json file."""
        self.ensure_dirs()
        obj = {
            "active_mmd_file": state.active_mmd_file,
            "active_mmd_id": state.active_mmd_id,
            "mmd_counter": state.mmd_counter,
            "last_session_key": state.last_session_key,
            "last_offloaded_tool_call_id": state.last_offloaded_tool_call_id,
            "last_l2_trigger_time": state.last_l2_trigger_time,
        }
        self._state_file.write_text(
            json.dumps(obj, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
