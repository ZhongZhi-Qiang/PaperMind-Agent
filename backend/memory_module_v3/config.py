"""memory_module_v3 configuration — reads from environment with sane defaults.

Memory backend selection via MEMORY_BACKEND env var:
  - "off"  : no long-term memory (default)
  - "v3"   : four-layer memory pyramid (L0→L1→L2→L3, auto-capture + auto-recall)

When MEMORY_BACKEND=v3, injection strategy is controlled by MEMORY_V3_INJECT:
  - "always" : force-inject retrieval context every turn (default)
  - "tool"   : register search_memory_v3 as an agent tool
  - "off"    : v3 enabled for API but no auto-injection
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Literal


def _env(key: str, default: str = "") -> str:
    return os.getenv(key, default).strip() or default


def _env_int(key: str, default: int) -> int:
    raw = os.getenv(key, "").strip()
    if raw:
        try:
            return int(raw)
        except ValueError:
            pass
    return default


def _env_bool(key: str, default: bool = False) -> bool:
    raw = os.getenv(key, "").strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    return default


def _env_float(key: str, default: float) -> float:
    raw = os.getenv(key, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


MemoryBackend = Literal["off", "v3"]


def get_memory_backend() -> MemoryBackend:
    """Unified switch: which memory backend is active."""
    raw = os.getenv("MEMORY_BACKEND", "").strip().lower()
    if raw in ("v3",):
        return raw  # type: ignore[return-value]
    return "off"


MemoryV3Inject = Literal["always", "tool", "off"]
MemoryV3RecallStrategy = Literal["hybrid", "keyword", "embedding"]


def get_memory_v3_inject_mode() -> MemoryV3Inject:
    raw = os.getenv("MEMORY_V3_INJECT", "").strip().lower()
    if raw in ("always", "tool", "off"):
        return raw  # type: ignore[return-value]
    return "always"


def get_memory_v3_recall_strategy() -> MemoryV3RecallStrategy:
    raw = os.getenv("MEMORY_V3_RECALL_STRATEGY", "").strip().lower()
    if raw in ("hybrid", "keyword", "embedding"):
        return raw  # type: ignore[return-value]
    return "hybrid"


@dataclass(frozen=True)
class MemoryV3Config:
    # L1 pipeline trigger
    pipeline_every_n: int = field(default_factory=lambda: _env_int("MEMORY_V3_PIPELINE_EVERY_N", 5))
    l1_idle_timeout_seconds: int = field(default_factory=lambda: _env_int("MEMORY_V3_L1_IDLE_TIMEOUT", 300))

    # L2 schedule
    l2_delay_after_l1_seconds: int = field(default_factory=lambda: _env_int("MEMORY_V3_L2_DELAY_AFTER_L1", 120))
    l2_max_interval_seconds: int = field(default_factory=lambda: _env_int("MEMORY_V3_L2_MAX_INTERVAL", 3600))

    # L3 trigger
    l3_trigger_every_n: int = field(default_factory=lambda: _env_int("MEMORY_V3_L3_TRIGGER_EVERY_N", 50))

    # Retrieval
    recall_strategy: MemoryV3RecallStrategy = field(default_factory=get_memory_v3_recall_strategy)
    dense_top_k: int = field(default_factory=lambda: _env_int("MEMORY_V3_DENSE_TOP_K", 20))
    keyword_top_k: int = field(default_factory=lambda: _env_int("MEMORY_V3_KEYWORD_TOP_K", 20))
    final_top_k: int = field(default_factory=lambda: _env_int("MEMORY_V3_FINAL_TOP_K", 10))
    rrf_k: int = field(default_factory=lambda: _env_int("MEMORY_V3_RRF_K", 60))
    inject_top_k: int = field(default_factory=lambda: _env_int("MEMORY_V3_INJECT_TOP_K", 5))
    max_chars_per_memory: int = field(default_factory=lambda: _env_int("MEMORY_V3_MAX_CHARS_PER_MEMORY", 500))
    max_total_recall_chars: int = field(default_factory=lambda: _env_int("MEMORY_V3_MAX_TOTAL_RECALL_CHARS", 3000))

    # Injection
    inject_mode: MemoryV3Inject = field(default_factory=get_memory_v3_inject_mode)

    # LLM extraction
    extract_batch_size: int = field(default_factory=lambda: _env_int("MEMORY_V3_EXTRACT_BATCH_SIZE", 20))

    # Embedding
    embedding_dimension: int = field(default_factory=lambda: _env_int("EMBEDDING_DIMENSION", 1024))

    # Symbolic offload (context compression)
    offload_enabled: bool = field(default_factory=lambda: _env_bool("MEMORY_V3_OFFLOAD_ENABLED", True))
    offload_threshold: int = field(default_factory=lambda: _env_int("MEMORY_V3_OFFLOAD_THRESHOLD", 500))

    # Symbolic Short-Term Memory pipeline (L1/L1.5/L2/L3)
    offload_force_trigger_threshold: int = field(default_factory=lambda: _env_int("MEMORY_V3_OFFLOAD_L1_THRESHOLD", 4))
    offload_l1_size_threshold: int = field(default_factory=lambda: _env_int("MEMORY_V3_OFFLOAD_L1_SIZE_THRESHOLD", 3000))
    offload_l2_null_threshold: int = field(default_factory=lambda: _env_int("MEMORY_V3_OFFLOAD_L2_NULL_THRESHOLD", 4))
    offload_l2_timeout_seconds: int = field(default_factory=lambda: _env_int("MEMORY_V3_OFFLOAD_L2_TIMEOUT", 300))
    offload_mild_ratio: float = field(default_factory=lambda: _env_float("MEMORY_V3_OFFLOAD_MILD_RATIO", 0.5))
    offload_aggressive_ratio: float = field(default_factory=lambda: _env_float("MEMORY_V3_OFFLOAD_AGGRESSIVE_RATIO", 0.85))
    offload_emergency_ratio: float = field(default_factory=lambda: _env_float("MEMORY_V3_OFFLOAD_EMERGENCY_RATIO", 0.95))
    offload_llm_provider: str = field(default_factory=lambda: _env("MEMORY_V3_OFFLOAD_LLM_PROVIDER", ""))
    offload_llm_model: str = field(default_factory=lambda: _env("MEMORY_V3_OFFLOAD_LLM_MODEL", ""))


def get_memory_v3_config() -> MemoryV3Config:
    return MemoryV3Config()
