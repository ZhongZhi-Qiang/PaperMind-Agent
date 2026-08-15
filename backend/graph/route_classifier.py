"""Rule-based query classifier for Smart Routing.

Three tiers:
  - L0 (trivial): greetings, thanks, chitchat → fast LLM direct reply
  - L1 (knowledge QA): paper questions, concepts → fast LLM + recall + read-only tools
  - L2 (complex): everything else → full pipeline

Conservative: when uncertain, route to L2.
"""

from __future__ import annotations

from enum import Enum


class RouteTier(str, Enum):
    L0 = "L0"  # trivial
    L1 = "L1"  # knowledge QA
    L2 = "L2"  # complex task


# Patterns that strongly indicate L0 (greetings, chitchat, thanks)
# NOTE: keep patterns specific enough to avoid false matches (e.g. "hi" matches "this")
L0_PATTERNS: tuple[str, ...] = (
    "你好", "谢谢", "hello", "thanks", "thank you",
    "再见", "bye", "goodbye", "see you",
    "晚安", "good night", "good morning",
    "你是谁", "who are you",
)

# Tools classified as read-only (safe for L1)
L1_READONLY_TOOLS: set[str] = {
    "read_file", "fetch_url", "query_wiki",
    "search_memory", "search_memory_v3", "read_scene",
    "list_wiki_pages", "read_wiki_page",
}

# Patterns that indicate L2 (code execution, file writes, multi-step tasks)
# NOTE: be specific — avoid matching read-only queries like "wiki中有多少论文"
L2_PATTERNS: tuple[str, ...] = (
    # Write / create / organize operations
    "运行", "execute", "run the code", "执行",
    "写文件", "write file", "create file", "保存", "save",
    "修改", "modify", "edit", "change the",
    "删除", "delete", "remove",
    "整理", "组织", "organize",
    "上传", "upload",
    "创建", "create a", "新建",
    "生成", "generate", "汇总",
    "终端", "terminal", "bash", "command",
    "python", "写代码", "write code",
    # Analysis / comparison (multi-step, tool-requiring)
    "analyze", "analyse", "分析",
    "compare", "comparison", "对比", "比较",
    "注册", "register source", "save wiki", "wiki整理", "wiki创建",
    "解析pdf", "parse pdf", "解析论文",
    "安装", "install", "部署", "deploy",
    "复现", "reproduce", "实现", "implement",
)


def classify_query(message: str) -> RouteTier:
    """Classify user query into L0/L1/L2 based on keyword matching.

    Conservative: when uncertain, route to L2.
    """
    lowered = message.strip().lower()

    # Guard: empty or very short input
    if len(lowered) < 2:
        return RouteTier.L0

    # Check L0 patterns first
    for pat in L0_PATTERNS:
        if pat in lowered:
            return RouteTier.L0

    # Check L2 patterns — any match → L2
    for pat in L2_PATTERNS:
        if pat in lowered:
            return RouteTier.L2

    # Short questions with question marks → L1 (knowledge QA)
    if ("?" in lowered or "？" in lowered) and len(lowered) < 150:
        return RouteTier.L1

    # Long messages → L2 (conservative)
    if len(lowered) > 200:
        return RouteTier.L2

    # Contains action verbs suggesting complex task → L2
    _action_indicators = ("帮我", "请帮我", "能不能", "可以帮我", "please", "can you")
    for ind in _action_indicators:
        if ind in lowered:
            return RouteTier.L2

    # Default: L1 (knowledge QA)
    return RouteTier.L1
