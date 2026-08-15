"""Tests for L1 fact deduplication (data consistency).

Covers the two-tier dedup contract:
- Tier 1: exact-content hash drops literal duplicates (zero LLM).
- Tier 2: semantic merge reuses the existing fact_id and unions source_msg_ids;
  any LLM/search failure defaults to `store` (never drop a fact).
"""

import asyncio
from unittest.mock import MagicMock

from memory_module_v3.extract.l1_dedup import L1Deduplicator
from memory_module_v3.storage.l1_repo import L1Fact, content_hash


def _fake_llm(response: str):
    async def llm_fn(system: str, user: str) -> str:
        return response

    return llm_fn


def _fake_search(candidates):
    async def search_fn(query: str, *, top_k: int = 3):
        return candidates

    return search_fn


class TestL1Dedup:
    def test_exact_duplicate_is_dropped(self):
        fact = L1Fact(content="用户研究方向是Transformer", fact_type="persona", source_msg_ids=[1])
        repo = MagicMock()
        repo.content_hash_index.return_value = {content_hash(fact.content)}
        result = asyncio.run(L1Deduplicator(repo).dedup([fact]))
        assert result == []

    def test_duplicate_within_batch_dropped(self):
        repo = MagicMock()
        repo.content_hash_index.return_value = set()
        dedup = L1Deduplicator(repo)
        result = asyncio.run(dedup.dedup([
            L1Fact(content="同一句话", source_msg_ids=[1]),
            L1Fact(content="同一句话", source_msg_ids=[2]),
        ]))
        assert len(result) == 1  # 批内第二条为精确重复

    def test_merge_reuses_fact_id_and_unions_source_msg_ids(self):
        new_fact = L1Fact(content="用户转向扩散模型", source_msg_ids=[3])
        repo = MagicMock()
        repo.content_hash_index.return_value = set()
        repo.get_by_id.return_value = {"fact_id": 7, "source_msg_ids": [1, 2]}
        dedup = L1Deduplicator(
            repo,
            llm_fn=_fake_llm('{"decision": "merge", "target_fact_id": 7}'),
            search_fn=_fake_search([{"fact_id": 7, "content": "用户做Transformer分类"}]),
        )
        result = asyncio.run(dedup.dedup([new_fact]))
        assert len(result) == 1
        assert result[0].fact_id == 7
        assert sorted(result[0].source_msg_ids) == [1, 2, 3]

    def test_store_leaves_fact_id_unset(self):
        new_fact = L1Fact(content="全新事实", source_msg_ids=[4])
        repo = MagicMock()
        repo.content_hash_index.return_value = set()
        repo.get_by_id.return_value = {"fact_id": 5, "source_msg_ids": [1]}
        dedup = L1Deduplicator(
            repo,
            llm_fn=_fake_llm('{"decision": "store"}'),
            search_fn=_fake_search([{"fact_id": 5, "content": "仅表面相似"}]),
        )
        result = asyncio.run(dedup.dedup([new_fact]))
        assert result[0].fact_id is None

    def test_llm_failure_defaults_to_store(self):
        async def bad_llm(system: str, user: str) -> str:
            raise RuntimeError("llm down")

        repo = MagicMock()
        repo.content_hash_index.return_value = set()
        dedup = L1Deduplicator(
            repo,
            llm_fn=bad_llm,
            search_fn=_fake_search([{"fact_id": 5, "content": "旧事实"}]),
        )
        result = asyncio.run(dedup.dedup([L1Fact(content="事实", source_msg_ids=[1])]))
        assert len(result) == 1
        assert result[0].fact_id is None

    def test_non_json_llm_output_defaults_to_store(self):
        repo = MagicMock()
        repo.content_hash_index.return_value = set()
        dedup = L1Deduplicator(
            repo,
            llm_fn=_fake_llm("抱歉，我无法解析这个输入"),
            search_fn=_fake_search([{"fact_id": 5, "content": "旧事实"}]),
        )
        result = asyncio.run(dedup.dedup([L1Fact(content="事实", source_msg_ids=[1])]))
        assert len(result) == 1
        assert result[0].fact_id is None

    def test_search_failure_defaults_to_store(self):
        async def bad_search(query: str, *, top_k: int = 3):
            raise RuntimeError("search down")

        repo = MagicMock()
        repo.content_hash_index.return_value = set()
        dedup = L1Deduplicator(
            repo,
            llm_fn=_fake_llm('{"decision": "merge", "target_fact_id": 9}'),
            search_fn=bad_search,
        )
        result = asyncio.run(dedup.dedup([L1Fact(content="事实", source_msg_ids=[1])]))
        assert len(result) == 1
        assert result[0].fact_id is None

    def test_no_candidates_skips_llm(self):
        calls = {"n": 0}

        async def counting_llm(system: str, user: str) -> str:
            calls["n"] += 1
            return '{"decision": "merge", "target_fact_id": 9}'

        repo = MagicMock()
        repo.content_hash_index.return_value = set()
        dedup = L1Deduplicator(repo, llm_fn=counting_llm, search_fn=_fake_search([]))
        result = asyncio.run(dedup.dedup([L1Fact(content="事实", source_msg_ids=[1])]))
        assert result[0].fact_id is None
        assert calls["n"] == 0  # 无候选 → 不调 LLM

    def test_merge_target_not_found_defaults_to_store(self):
        repo = MagicMock()
        repo.content_hash_index.return_value = set()
        repo.get_by_id.return_value = None  # 目标 fact 不存在
        dedup = L1Deduplicator(
            repo,
            llm_fn=_fake_llm('{"decision": "merge", "target_fact_id": 42}'),
            search_fn=_fake_search([{"fact_id": 42, "content": "旧事实"}]),
        )
        result = asyncio.run(dedup.dedup([L1Fact(content="事实", source_msg_ids=[1])]))
        assert len(result) == 1
        assert result[0].fact_id is None
