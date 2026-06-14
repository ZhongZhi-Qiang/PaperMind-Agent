"""L1 Summarization Prompt — ported from TencentDB-Agent-Memory l1-prompt.ts.

Converts tool call/result pairs into high-density JSON summaries.
"""

from __future__ import annotations

import json
from typing import Any


L1_SYSTEM_PROMPT = """你是一个专为 AI 编码助手提供支持的"工具结果摘要器"。你的核心任务是深度理解当前的对话上下文，并将繁杂的工具调用与执行结果（一对toolcall和tool result整合成一条summary输出），提炼为高信息密度的 JSON 数组。

在生成摘要前，请务必进行以下内部思考：
1. 任务对齐：结合最近的对话记录，识别用户当前的核心目标和最新意图。若上下文存在冲突，始终以最新的用户意图为准。
2. 价值过滤：忽略工具如何工作的冗余细节，直接提取"发现了什么关键线索"、"做了什么关键动作"、"修改了什么具体内容"或"遇到了什么具体报错"。
3. 影响评估：判断该结果对当前任务的实质性影响（例如：证实了某个假设、推进了哪一步、做出了什么决策，或因为什么报错导致了阻塞）。

【输出格式要求】
你必须且只能输出一个合法的 JSON 对象数组 [{...}]，每个对象**必须**包含以下字段：
- "tool_call": 工具调用的简洁描述。处理规则如下：
  · 如果输入中该 tool pair 标记了 [NEEDS_COMPRESS]，你必须将工具名+关键参数压缩为一句简洁的描述（≤150字符），保留工具名、操作目标（如文件路径、命令意图），省略内联脚本/大段内容的细节。
  · 如果未标记 [NEEDS_COMPRESS]，直接简述工具与参数即可。
- "summary": 融合上述思考的精炼总结（≤200个字符）。必须一针见血地说清楚结果的业务价值，以及它对任务的推进/阻塞作用。
- "tool_call_id": 原始的 tool_call_id（必须原样透传）。
- "timestamp": 原始的中国标准时间（+08:00）ISO 8601 时间戳（必须原样透传）。
- "score"（**必填**）: 结合信息密度和任务目的分析summary对于原文的可替代性，范围在0-10之间，越接近10表示summary越能替代原文。

【严格规则】
只允许输出纯 JSON 数组，严禁输出思考过程或其他解释性文本。"""


_PARAMS_MAX_LEN = 500
_RESULT_MAX_LEN = 2000
_COMPRESS_THRESHOLD = 200


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False)
    except Exception:
        return str(value)


def _truncate(s: str, max_len: int) -> str:
    if len(s) <= max_len:
        return s
    return s[:max_len] + "..."


def build_l1_user_prompt(
    recent_messages: str,
    pairs: list[dict[str, Any]],
) -> str:
    """Build the L1 user prompt for summarization.

    Args:
        recent_messages: Formatted recent conversation context
        pairs: List of dicts with keys: tool_name, tool_call_id, params, result, timestamp
    """
    parts: list[str] = []

    parts.append("## 最近的对话上下文（用于理解当前任务）：")
    parts.append(recent_messages)
    parts.append("\n## Tool call/result pairs to summarize:")

    for i, p in enumerate(pairs):
        params_str = _truncate(_stringify(p.get("params", "")), _PARAMS_MAX_LEN)
        result_str = _truncate(_stringify(p.get("result", "")), _RESULT_MAX_LEN)
        canonical = f"{p.get('tool_name', 'tool')}({_stringify(p.get('params', ''))})"
        needs_compress = len(canonical) > _COMPRESS_THRESHOLD

        parts.append(f"--- Tool Pair {i + 1} ---")
        parts.append(f"tool_call_id: {p.get('tool_call_id', '')}")
        parts.append(f"timestamp: {p.get('timestamp', '')}")
        if needs_compress:
            parts.append(f"Tool: {p.get('tool_name', 'tool')} [NEEDS_COMPRESS]")
        else:
            parts.append(f"Tool: {p.get('tool_name', 'tool')}")
        parts.append(f"Params: {params_str}")
        parts.append(f"Result: {result_str}\n")

    parts.append("Summarize each pair into the JSON array format described.")
    return "\n".join(parts)
