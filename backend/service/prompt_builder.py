from __future__ import annotations

from pathlib import Path

from config import get_settings
from memory_module_v3.config import get_memory_backend

SYSTEM_COMPONENTS: tuple[tuple[str, str], ...] = (
    ("Skills Snapshot", "skills/SKILLS_SNAPSHOT.md"),
    ("Soul", "workspace/SOUL.md"),
    ("Identity", "workspace/IDENTITY.md"),
    ("User Profile", "workspace/USER.md"),
    ("Long-Term Memory", "workspace/MEMORY.md"),
    ("Agents Guide", "workspace/AGENTS.md"),
)

_MEMORY_HINTS = {
    "off": None,
    "v3": (
        "<!-- Long-term Memory (v3) -->\n"
        "系统采用四层记忆金字塔（L0 原始对话 → L1 结构化事实 → L2 主题知识 → L3 人格画像），"
        "自动捕获对话并分层沉淀。检索通过 hybrid（pgvector + tsvector + RRF）自动完成。\n"
        "此外，workspace/MEMORY.md 记录了跨会话的可复用经验、用户偏好和系统行为特征，"
        "你在回答前应参考其中的内容。当任务失败后切换方案成功时，"
        "使用 retry-lesson-capture 技能将经验沉淀到 workspace/MEMORY.md。"
    ),
}


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n...[truncated]"


def _read_component(base_dir: Path, relative_path: str, limit: int) -> str:
    path = base_dir / relative_path
    if not path.exists():
        return f"[missing component: {relative_path}]"
    return _truncate(path.read_text(encoding="utf-8"), limit)


def _get_memory_hint_key() -> str:
    backend = get_memory_backend()
    if backend == "v3":
        return "v3"
    return "off"


def build_system_prompt(base_dir: Path) -> str:
    settings = get_settings()
    parts: list[str] = []

    for label, relative_path in SYSTEM_COMPONENTS:
        content = _read_component(base_dir, relative_path, settings.component_char_limit)
        parts.append(f"<!-- {label} -->\n{content}")

    hint_key = _get_memory_hint_key()

    memory_hint = _MEMORY_HINTS.get(hint_key)
    if memory_hint:
        parts.append(memory_hint)

    return "\n\n".join(parts)
