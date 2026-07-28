"""通过工厂按配置创建 Agent（checkpointer、tools、prompt、middleware）。"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from langchain.agents import create_agent
from langchain.agents.middleware import SummarizationMiddleware
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.tools import BaseTool

from graph.guardian import build_guardian_middleware
from graph.context_offload import build_context_offload_middleware
from graph.harness_security import build_harness_security_middleware
from graph.harness_review import build_harness_review_middleware
from graph.checkpointer import get_checkpointer
from graph.parallel_tools import patch_agent_for_parallel_tools
from service.prompt_builder import build_system_prompt
from graph.llm import build_llm_config_from_settings, get_llm

# 兼容 LangGraph 的 CompiledStateGraph 类型
AgentGraph = Any

logger = logging.getLogger(__name__)

# 中文摘要 prompt，适配中文对话场景
SUMMARY_PROMPT_ZH = """你是上下文提取助手。你的唯一任务是从以下对话历史中提取最高质量的上下文信息。

你即将达到输入 token 上限，必须从对话历史中提取最关键的信息。
提取的上下文将替换下方的对话历史，因此只保留对继续工作最重要的内容。

请使用以下结构组织摘要，每个部分必须填写具体内容或明确标注"无"：

## 会话目标
用户的主要目标或请求是什么？你要完成什么任务？

## 摘要
记录对话中最重要的上下文，包括关键选择、结论、策略，以及重要决策的理由。记录被否决的方案及原因。

## 已确认的关键事实
列出对话中**已通过工具验证**的事实（不是猜测），例如：
- 已读取的文件及其关键结论
- 已确认的根因（如"BOM 导致 frontmatter 解析失败"）
- 已排除的假设（如"文件实际在 wiki/methods/ 而非 wiki/papers/"）
这些事实在后续工作中**不应再被重新验证**，直接使用即可。

## 产出物
创建、修改或访问了哪些文件、资源？列出具体文件路径和变更描述。

## 后续步骤
还有哪些具体任务待完成？下一步应该做什么？列出明确的 action items。

请仔细阅读以下对话历史，提取最重要的上下文来替换它，以释放对话空间。
只输出提取的上下文，不要包含任何额外的解释或说明。

<messages>
{messages}
</messages>"""

# 默认：消息数达到 50 触发压缩，保留最近 20 条
DEFAULT_SUMMARIZATION_TRIGGER_MESSAGES = 80
DEFAULT_SUMMARIZATION_KEEP_MESSAGES = 30


def _summarization_trigger_messages() -> int:
    v = os.getenv("SUMMARIZATION_TRIGGER_MESSAGES", "").strip()
    if not v:
        return DEFAULT_SUMMARIZATION_TRIGGER_MESSAGES
    try:
        return max(1, int(v))
    except ValueError:
        return DEFAULT_SUMMARIZATION_TRIGGER_MESSAGES


def _summarization_keep_messages() -> int:
    v = os.getenv("SUMMARIZATION_KEEP_MESSAGES", "").strip()
    if not v:
        return DEFAULT_SUMMARIZATION_KEEP_MESSAGES
    try:
        return max(1, int(v))
    except ValueError:
        return DEFAULT_SUMMARIZATION_KEEP_MESSAGES


@dataclass
class AgentConfig:
    """Agent 构建所需配置。"""

    llm: BaseChatModel
    tools: list[BaseTool]
    system_prompt: str
    checkpointer: Any | None = None
    guardian_enabled: bool = True
    use_summarization: bool = False
    summarization_trigger_messages: int = DEFAULT_SUMMARIZATION_TRIGGER_MESSAGES
    summarization_keep_messages: int = DEFAULT_SUMMARIZATION_KEEP_MESSAGES
    # Context offload: compress verbose tool results before LLM sees them
    offload_enabled: bool = False
    offload_threshold: int = 500
    offload_llm: Any | None = None
    offload_config: dict = field(default_factory=dict)
    harness_security_enabled: bool = True
    harness_review_enabled: bool = True


def build_agent_config(
    base_dir: Path,
    tools: list[BaseTool],
    *,
    use_checkpointer: bool = True,
    use_summarization: bool | None = None,
) -> AgentConfig:
    """从当前运行配置与 base_dir、tools 构建 AgentConfig。"""
    from config import get_settings

    settings = get_settings()
    prompt = build_system_prompt(base_dir) if base_dir else ""
    llm = get_llm(build_llm_config_from_settings(settings, temperature=0.0, streaming=True))
    checkpointer = get_checkpointer() if use_checkpointer else None
    if use_summarization is None:
        use_summarization = os.getenv("SUMMARIZATION_ENABLED", "false").strip().lower() in ("true", "1", "yes")

    # Context offload: enabled when memory backend is v3 and offload is on
    from memory_module_v3.config import get_memory_backend, get_memory_v3_config
    offload_enabled = False
    offload_threshold = 500
    offload_llm = None
    offload_config: dict = {}
    if get_memory_backend() == "v3":
        try:
            v3_cfg = get_memory_v3_config()
            offload_enabled = v3_cfg.offload_enabled
            offload_threshold = v3_cfg.offload_threshold
            offload_config = {
                "force_trigger_threshold": v3_cfg.offload_force_trigger_threshold,
                "l1_size_threshold": v3_cfg.offload_l1_size_threshold,
                "l2_null_threshold": v3_cfg.offload_l2_null_threshold,
                "l2_timeout_seconds": v3_cfg.offload_l2_timeout_seconds,
                "mild_offload_ratio": v3_cfg.offload_mild_ratio,
                "aggressive_compress_ratio": v3_cfg.offload_aggressive_ratio,
                "emergency_compress_ratio": v3_cfg.offload_emergency_ratio,
            }
            # Build offload LLM (separate model saves tokens)
            if offload_enabled:
                try:
                    from graph.llm import ResolvedLLMConfig, get_fast_llm
                    offload_provider = v3_cfg.offload_llm_provider
                    offload_model = v3_cfg.offload_llm_model
                    if offload_provider and offload_model:
                        # Use dedicated offload model — resolve via settings
                        from config.config import _normalize_provider, _resolve_llm_api_key, _resolve_llm_base_url
                        norm_provider = _normalize_provider(offload_provider)
                        offload_llm = get_llm(ResolvedLLMConfig(
                            provider=norm_provider,
                            model=offload_model,
                            api_key=_resolve_llm_api_key(norm_provider),
                            base_url=_resolve_llm_base_url(norm_provider),
                            temperature=0.2,
                            streaming=False,
                        ))
                    else:
                        # Prefer fast LLM for offload (saves tokens/latency); fall back to main
                        try:
                            offload_llm = get_fast_llm(settings, temperature=0.2, streaming=False)
                        except Exception:
                            offload_llm = llm
                except Exception as llm_exc:
                    logger.warning("Failed to create offload LLM: %s, using main LLM", llm_exc)
                    offload_llm = llm
        except Exception:
            pass

    harness_security_enabled = settings.harness_security_enabled and settings.harness_enabled
    harness_review_enabled = settings.harness_review_enabled and settings.harness_enabled

    return AgentConfig(
        llm=llm,
        tools=tools,
        system_prompt=prompt,
        checkpointer=checkpointer,
        guardian_enabled=settings.guardian_enabled,
        use_summarization=use_summarization,
        summarization_trigger_messages=_summarization_trigger_messages(),
        summarization_keep_messages=_summarization_keep_messages(),
        offload_enabled=offload_enabled,
        offload_threshold=offload_threshold,
        offload_llm=offload_llm,
        offload_config=offload_config,
        harness_security_enabled=harness_security_enabled,
        harness_review_enabled=harness_review_enabled,
    )


def create_agent_from_config(config: AgentConfig) -> AgentGraph:
    """根据 AgentConfig 创建带 checkpointer、可选 Guardian / Summarization / Offload 的 agent graph。"""
    middleware: list[Any] = []
    if config.guardian_enabled:
        middleware.append(build_guardian_middleware())
    if config.harness_security_enabled:
        middleware.append(build_harness_security_middleware())
    if config.offload_enabled:
        middleware.append(
            build_context_offload_middleware(
                llm=config.offload_llm,
                threshold=config.offload_threshold,
                config=config.offload_config,
            )
        )
    if config.use_summarization:
        middleware.append(
            SummarizationMiddleware(
                model=config.llm,
                trigger=("messages", config.summarization_trigger_messages),
                keep=("messages", config.summarization_keep_messages),
                summary_prompt=SUMMARY_PROMPT_ZH,
            )
        )
    if config.harness_review_enabled:
        middleware.append(build_harness_review_middleware())
    agent = create_agent(
        model=config.llm,
        tools=config.tools,
        system_prompt=config.system_prompt,
        checkpointer=config.checkpointer,
        middleware=middleware if middleware else (),
    )
    # Patch tools node for parallel execution (non-destructive: falls back if graph structure differs)
    from graph.parallel_tools import patch_agent_for_parallel_tools
    agent = patch_agent_for_parallel_tools(agent, config.tools)
    return agent

