"""L1.5 Task Judgment Prompt — ported from TencentDB-Agent-Memory l15-prompt.ts.

Determines task lifecycle: completion, continuation, new task detection.
"""

from __future__ import annotations

from typing import Any


L15_SYSTEM_PROMPT = """你是一个面向 AI 编码助手的"任务生命周期门神"。
你的职责是交叉分析提供的三个输入源，精准研判任务状态，并输出纯 JSON 对象。

【输入数据利用指南（必须遵循的思考链路）】
1. 第一步 - 剖析 recentMessages（识别意图）：根据当前和历史对话，提取用户最新回复的核心诉求。判断是"继续排查"、"宣布完工（如：跑通了）"、"单轮闲聊问答"还是"开启全新需求"。
2. 第二步 - 对齐 currentMmd（评估当前基线）：将用户的最新意图与 currentMmd 的完整 Mermaid 内容进行比对——关注 taskGoal、各节点的 status（done/doing/todo）以及 summary。如果诉求完全超出了当前图表的范畴或目标已实现（所有节点 done 且无后续），则 taskCompleted 为 true。若仍在解决图表中的子问题（包括 doing 节点或修 bug），则为 false。(如果没有currentMmd，就只根据当前对话和历史对话来判断是否继续任务)
3. 第三步 - 检索 availableMmds（判断是否延续）：如果判定要开启新任务（isLongTask=true 且 taskCompleted=true/当前无任务），必须扫描 availableMmds 的 taskGoal 和时间信息。若新诉求与列表中某个旧任务高度重合（如回到昨天没做完的模块），则是延续（isContinuation=true）。

【严格 JSON 输出格式】
务必输出合法的纯 JSON 对象，格式如下：
{
  "taskCompleted": boolean,
  "isLongTask": boolean,
  "isContinuation": boolean,
  "continuationMmdFile": "string|null",
  "newTaskLabel": "string|null"
}

只输出纯 JSON 对象，绝不允许包含解释文字。"""


def build_l15_user_prompt(
    recent_messages: str,
    current_mmd: dict[str, str] | None,
    available_mmds: list[dict[str, Any]],
) -> str:
    """Build the L1.5 user prompt for task judgment.

    Args:
        recent_messages: Formatted recent conversation context (last 6 messages)
        current_mmd: Dict with 'filename', 'content', 'path' or None
        available_mmds: List of dicts with 'filename', 'path', 'taskGoal', etc.
    """
    parts: list[str] = []

    parts.append("## 1. 最近的对话上下文 (Recent messages):")
    parts.append(recent_messages)
    parts.append("\n## 2. 当前挂载的任务图 (Active Mermaid — 完整内容):")

    if current_mmd and current_mmd.get("filename"):
        parts.append(f"**File:** {current_mmd['filename']}")
        if current_mmd.get("path"):
            parts.append(f"**Path:** `{current_mmd['path']}`")
        parts.append(f"\n```mermaid\n{current_mmd.get('content', '')}\n```")
    else:
        parts.append("(none - 当前处于闲置状态，无活跃任务)")

    parts.append("\n## 3. 历史可用的任务图 (Available Mermaid task files):")

    if not available_mmds:
        parts.append("(none - 暂无历史长任务)")
    else:
        for m in available_mmds:
            parts.append(f"- **{m.get('filename', '')}**")
            parts.append(f"  path: `{m.get('path', '')}`")
            parts.append(f"  taskGoal: {m.get('taskGoal', '')}")
            total = m.get('doneCount', 0) + m.get('doingCount', 0) + m.get('todoCount', 0)
            parts.append(f"  progress: {m.get('doneCount', 0)}/{total} done, {m.get('doingCount', 0)} doing, {m.get('todoCount', 0)} todo")
            if m.get('updatedTime'):
                parts.append(f"  lastUpdated: {m['updatedTime']}")
            if m.get('nodeSummaries'):
                parts.append("  recentNodes:")
                for n in m['nodeSummaries']:
                    parts.append(f"    - [{n.get('nodeId', '')}] ({n.get('status', '')}) {n.get('summary', '')}")
            parts.append("")

    parts.append("请严格根据系统指令的【三步思考链路】进行研判，并输出合法的 JSON 对象。")
    return "\n".join(parts)
