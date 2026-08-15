"""Tests for Offload L3 progressive compression (data consistency).

Covers the invariant from commit 1533533: L3 compression must NEVER delete or
mechanically truncate messages — it only replaces matched tool results with
high-density summaries, prioritized by score × length.
"""

from unittest.mock import MagicMock

from langchain_core.messages import AIMessage, ToolMessage

from memory_module_v3.offload.pipeline import OffloadPipeline
from memory_module_v3.offload.storage import OffloadStorage
from memory_module_v3.offload.types import OffloadEntry


def _build_pipeline(tmp_path, entries):
    storage = OffloadStorage(tmp_path, session_id="test")
    storage.append_entries(entries)
    return OffloadPipeline(llm=MagicMock(), storage=storage, config={})


def _tool_call(content: str, call_id: str) -> ToolMessage:
    return ToolMessage(content=content, tool_call_id=call_id, name="read_file")


class TestOffloadL3:
    def test_low_pressure_returns_unchanged(self, tmp_path):
        pipeline = _build_pipeline(tmp_path, [
            OffloadEntry(timestamp="t", summary="s1", result_ref="refs/a.md", tool_call_id="c1", score=9),
        ])
        msgs = [_tool_call("A" * 500, "c1")]
        new_msgs, count = pipeline.compress_messages_by_score(msgs, context_ratio=0.4)
        assert count == 0
        assert len(new_msgs) == 1
        assert "Offloaded Tool Result" not in new_msgs[0].content

    def test_high_pressure_replaces_high_score_only_and_never_deletes(self, tmp_path):
        pipeline = _build_pipeline(tmp_path, [
            OffloadEntry(timestamp="t", summary="高替", result_ref="refs/a.md", tool_call_id="c1", score=9),
            OffloadEntry(timestamp="t", summary="低分", result_ref="refs/b.md", tool_call_id="c2", score=1),
        ])
        msgs = [
            AIMessage(content="hi"),
            _tool_call("A" * 500, "c1"),
            _tool_call("B" * 100, "c2"),
        ]
        new_msgs, count = pipeline.compress_messages_by_score(msgs, context_ratio=0.9)
        # ratio=0.9 → min_score=2, min_length=50：c1(score=9,len=500) 被替换，c2(score=1) 不动
        assert count == 1
        assert len(new_msgs) == len(msgs)  # 永不删除
        assert "Offloaded Tool Result" in new_msgs[1].content
        assert "Offloaded Tool Result" not in new_msgs[2].content

    def test_short_message_below_min_length_not_compressed(self, tmp_path):
        pipeline = _build_pipeline(tmp_path, [
            OffloadEntry(timestamp="t", summary="s", result_ref="refs/a.md", tool_call_id="c1", score=9),
        ])
        msgs = [_tool_call("short", "c1")]
        new_msgs, count = pipeline.compress_messages_by_score(msgs, context_ratio=0.9)
        assert count == 0
        assert new_msgs[0].content == "short"

    def test_already_offloaded_message_not_recompressed(self, tmp_path):
        pipeline = _build_pipeline(tmp_path, [
            OffloadEntry(timestamp="t", summary="s", result_ref="refs/a.md", tool_call_id="c1", score=9),
        ])
        already = "[Offloaded Tool Result | node: N/A]\nSummary: 已是摘要"
        msgs = [_tool_call(already, "c1")]
        new_msgs, count = pipeline.compress_messages_by_score(msgs, context_ratio=0.9)
        assert count == 0  # 已 offload 的标记阻止重复压缩
        assert new_msgs[0].content == already
