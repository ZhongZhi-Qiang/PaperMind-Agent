"""End-to-end latency benchmark for AgentManager.astream.

Runs N turns of canned academic questions, parsing the `latency ...` log lines
emitted by graph/agent.py to report per-stage timings.

Usage (from backend/):
    python scripts/benchmark_latency.py --turns 5
    python scripts/benchmark_latency.py --turns 5 --message "解释一下注意力机制"

NOTE: requires a working LLM endpoint + Postgres (for v3 memory).
Logs emitted by the agent are captured via the root logger; ensure
`logging` is configured at INFO before running.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import statistics
import sys
import time
import re
from pathlib import Path
from typing import Any

# Ensure backend/ is importable when running as a script
_BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))


# Captures: stage, session, value_ms (and optionally total_ms)
_LATENCY_RE = re.compile(r"latency session=(\S+) (\w+)_ms=([\d.]+)")

DEFAULT_QUESTIONS = [
    "什么是 Transformer 中的多头注意力？",
    "解释一下 BERT 和 GPT 的核心差异。",
    "对比一下 RAG 和微调两种方案。",
    "如何评估一个检索系统的召回质量？",
    "数据集偏差对模型泛化有什么影响？",
]


class _LatencyCollector:
    """Subscribe to logger output and pull out latency numbers per stage."""

    def __init__(self) -> None:
        self.records: dict[str, list[float]] = {
            "recall": [],
            "first_token": [],
            "ttft": [],
            "done": [],
            "total": [],
            "cache_hit": [],
        }

    def __call__(self, record: logging.LogRecord) -> None:
        msg = record.getMessage()
        m = _LATENCY_RE.search(msg)
        if not m:
            return
        stage = m.group(2)
        value = float(m.group(3))
        if stage in self.records:
            self.records[stage].append(value)


async def _run_once(agent_manager: Any, message: str, session_id: str) -> str:
    """Drive one astream turn, return final content."""
    from graph.context import build_request_context

    ctx = build_request_context(thread_id=session_id, include_langfuse=False)
    final = ""
    async for event in agent_manager.astream(message, history=[], context=ctx):
        etype = event.get("type")
        if etype == "token":
            final += event.get("content", "")
        elif etype == "done":
            final = event.get("content", final)
    return final


def _summary(name: str, values: list[float]) -> str:
    if not values:
        return f"{name:12s} n=0"
    return (
        f"{name:12s} n={len(values)} "
        f"mean={statistics.mean(values):7.0f}ms "
        f"median={statistics.median(values):7.0f}ms "
        f"max={max(values):7.0f}ms"
    )


async def main(turns: int, message: str | None) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    collector = _LatencyCollector()
    # Use a custom handler that feeds the collector
    class _Sink(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            collector(record)

    sink = _Sink(level=logging.INFO)
    logging.getLogger().addHandler(sink)

    # Import after logging setup so we capture agent init logs too
    from graph.agent import agent_manager
    from pathlib import Path

    backend_dir = Path(__file__).resolve().parent.parent
    agent_manager.initialize(backend_dir)

    questions = ([message] * turns) if message else (
        DEFAULT_QUESTIONS[:turns] if turns <= len(DEFAULT_QUESTIONS)
        else [DEFAULT_QUESTIONS[i % len(DEFAULT_QUESTIONS)] for i in range(turns)]
    )

    print(f"\n=== Running {len(questions)} turns ===\n")
    for i, q in enumerate(questions):
        print(f"[turn {i+1}] {q}")
        t_start = time.perf_counter()
        answer = await _run_once(agent_manager, q, session_id=f"bench-{i}")
        wall = (time.perf_counter() - t_start) * 1000
        print(f"  wall={wall:.0f}ms answer_preview={answer[:80]!r}\n")

    print("\n=== Latency summary (from agent埋点) ===")
    for stage in ("recall", "first_token", "ttft", "done", "total", "cache_hit"):
        print(_summary(stage, collector.records.get(stage, [])))
    hits = collector.records.get("cache_hit", [])
    if hits:
        print(f"\nQA cache hit rate: {len(hits)}/{len(collector.records.get('total', []))} turns")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--turns", type=int, default=5, help="Number of questions to run")
    parser.add_argument("--message", type=str, default=None, help="Single canned message (overrides defaults)")
    args = parser.parse_args()
    asyncio.run(main(args.turns, args.message))