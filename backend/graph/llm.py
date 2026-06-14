from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_community.chat_models.tongyi import ChatTongyi

try:
    from langchain_deepseek import ChatDeepSeek
except ImportError:  # pragma: no cover - optional dependency at runtime
    ChatDeepSeek = None

from config import Settings


@dataclass(frozen=True)
class ResolvedLLMConfig:
    provider: str
    model: str
    api_key: str | None
    base_url: str
    temperature: float = 0.6
    streaming: bool = False


@dataclass(frozen=True)
class ResolvedEmbeddingConfig:
    provider: str
    model: str
    api_key: str | None
    base_url: str


def _ensure_api_key(config: ResolvedLLMConfig) -> None:
    if not config.api_key:
        raise RuntimeError(f"Missing API key for provider {config.provider}")


def _build_openai_compatible_chat(config: ResolvedLLMConfig) -> BaseChatModel:
    _ensure_api_key(config)
    kwargs: dict = {}
    if config.streaming:
        kwargs["model_kwargs"] = {"stream_options": {"include_usage": True}}
    # 推理模型（如 mimo-v2.5-pro）默认开启 thinking 模式，
    # 需要在 extra_body 中传入 thinking: {"enabled": false} 来关闭，
    # 否则 API 要求每轮都回传 reasoning_content，LangChain 不支持。
    kwargs["extra_body"] = {"enable_thinking": False}
    # 显式禁用代理（Windows 系统代理会导致连接超时）并增加超时
    import httpx
    kwargs["http_client"] = httpx.Client(proxy=None, timeout=120.0)
    kwargs["request_timeout"] = 120.0
    return ChatOpenAI(
        model=config.model,
        api_key=config.api_key,
        base_url=config.base_url,
        temperature=config.temperature,
        streaming=config.streaming,
        **kwargs,
    )


def _build_tongyi_chat(config: ResolvedLLMConfig) -> BaseChatModel:
    """DashScope（通义千问）官方 SDK 客户端（非 OpenAI 兼容模式）。"""
    _ensure_api_key(config)
    # ChatTongyi 不使用 base_url；通过 dashscope SDK 直连阿里云 DashScope
    return ChatTongyi(
        model=config.model,
        api_key=config.api_key,
        streaming=True,
        model_kwargs={"temperature": config.temperature},
    )


def _build_deepseek_chat(config: ResolvedLLMConfig) -> BaseChatModel:
    if ChatDeepSeek is None:
        raise RuntimeError("langchain-deepseek is not installed")
    _ensure_api_key(config)
    return ChatDeepSeek(
        model=config.model,
        api_key=config.api_key,
        base_url=config.base_url,
        temperature=config.temperature,
    )


LLM_REGISTRY: Dict[str, Callable[[ResolvedLLMConfig], BaseChatModel]] = {
    # OpenAI 及兼容模式
    "openai": _build_openai_compatible_chat,
    "zhipu": _build_openai_compatible_chat,
    # DashScope / Bailian / Qwen：使用官方 dashscope SDK（ChatTongyi）
    "bailian": _build_openai_compatible_chat,
    # "bailian": _build_tongyi_chat,
    # "dashscope": _build_tongyi_chat,
    # "qwen": _build_tongyi_chat,
    # DeepSeek 专用客户端
    "deepseek": _build_deepseek_chat,
}


def build_llm_config_from_settings(
    settings: Settings,
    *,
    temperature: float = 0.0,
    streaming: bool = False,
) -> ResolvedLLMConfig:
    return ResolvedLLMConfig(
        provider=settings.llm_provider,
        model=settings.llm_model,
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        temperature=temperature,
        streaming=streaming,
    )


def get_llm(config: ResolvedLLMConfig) -> BaseChatModel:
    provider = config.provider
    if provider not in LLM_REGISTRY:
        raise RuntimeError(f"Unsupported LLM provider: {provider}")
    factory = LLM_REGISTRY[provider]
    return factory(config)


def build_embedding_config_from_settings(settings: Settings) -> ResolvedEmbeddingConfig:
    return ResolvedEmbeddingConfig(
        provider=settings.embedding_provider,
        model=settings.embedding_model,
        api_key=settings.embedding_api_key,
        base_url=settings.embedding_base_url,
    )


class _DashScopeEmbeddings:
    """DashScope embedding via OpenAI-compatible /embeddings endpoint.

    Replaces langchain_community.embeddings.DashScopeEmbeddings which passes
    data in an incompatible format (causes 400: "contents is neither str nor
    list of str").  This class calls the DashScope API directly via httpx.
    """

    def __init__(self, model: str, api_key: str):
        import httpx
        self._model = model
        self._api_key = api_key
        self._base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
        self._client = httpx.Client(proxy=None, timeout=60.0, trust_env=False)

    def _call_api(self, texts: list[str]) -> list[list[float]]:
        resp = self._client.post(
            f"{self._base_url}/embeddings",
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            json={"model": self._model, "input": texts},
        )
        resp.raise_for_status()
        data = resp.json()
        return [item["embedding"] for item in sorted(data["data"], key=lambda x: x["index"])]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        all_embeddings: list[list[float]] = []
        for i in range(0, len(texts), 10):
            batch = texts[i : i + 10]
            all_embeddings.extend(self._call_api(batch))
        return all_embeddings

    def embed_query(self, text: str) -> list[float]:
        return self._call_api([text])[0]


class _LocalONNXEmbeddings:
    """LangChain-compatible wrapper for Chroma's built-in ONNX MiniLM-L6-v2."""

    def __init__(self):
        from chromadb.utils.embedding_functions import ONNXMiniLM_L6_V2
        self._ef = ONNXMiniLM_L6_V2()

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._ef(texts)

    def embed_query(self, text: str) -> list[float]:
        return self._ef([text])[0]


def get_embedding_model(config: ResolvedEmbeddingConfig):
    """
    构建统一的 Embedding 实例。

    - local:  使用 Chroma 内置 ONNX MiniLM-L6-v2（无需 API key）
    - openai: 使用 OpenAIEmbeddings（支持 base_url）
    - bailian/dashscope/qwen: 使用自定义 _DashScopeEmbeddings（httpx 直调 DashScope API）
    """
    provider = (config.provider or "").strip().lower()

    if provider == "local":
        return _LocalONNXEmbeddings()

    if not config.api_key:
        raise RuntimeError(f"Missing embedding API key for provider {config.provider}")

    if provider in {"bailian", "dashscope", "qwen"} or "dashscope" in (config.base_url or "").lower():
        return _DashScopeEmbeddings(model=config.model, api_key=config.api_key)

    return OpenAIEmbeddings(model=config.model, api_key=config.api_key, base_url=config.base_url)

