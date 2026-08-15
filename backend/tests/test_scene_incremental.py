"""Tests for incremental L2 scene consolidation (data consistency).

Focus on the invariants that have regressed in the past (commit 99d97d8):
- merging new facts into an existing scene must NOT drop previously owned fact_ids
- `merged_from` folding must delete the folded scene while transferring its fact_ids
- create routing must actually add a brand-new scene
- LLM failure must leave existing scenes untouched
"""

import asyncio
import json

from memory_module_v3.consolidate.scene_extractor import SceneExtractor
from memory_module_v3.storage.l2_file_repo import L2FileRepo, L2Scene


class FakeL1Repo:
    """In-memory L1 repo stub implementing what SceneExtractor uses."""

    def __init__(self, facts):
        self._facts = facts

    def get_all(self, limit: int = 1000):
        return self._facts

    def get_since(self, since, limit: int = 500):
        return self._facts


def _fake_llm(payload: str):
    async def llm_fn(system: str, user: str) -> str:
        return payload

    return llm_fn


class TestSceneIncremental:
    def test_update_merges_fact_ids_without_loss(self, tmp_path):
        repo = L2FileRepo(tmp_path)
        repo.upsert(L2Scene(scene_name="transformer", summary="old", content_md="old", fact_ids=[1, 2]))

        new_facts = [{"fact_id": 51, "fact_type": "episodic", "content": "新事实", "scene_name": "transformer"}]
        payload = json.dumps([{
            "scene_name": "transformer",
            "action": "update",
            "summary": "updated",
            "added_fact_ids": [51],
            "content": "new content",
        }])
        extractor = SceneExtractor(FakeL1Repo(new_facts), repo, _fake_llm(payload))

        asyncio.run(extractor.consolidate_incremental(new_facts))

        assert sorted(repo.get_fact_ids("transformer")) == [1, 2, 51]

    def test_create_adds_new_scene(self, tmp_path):
        repo = L2FileRepo(tmp_path)
        repo.upsert(L2Scene(scene_name="transformer", summary="s", content_md="c", fact_ids=[1]))

        new_facts = [{"fact_id": 61, "fact_type": "episodic", "content": "扩散模型", "scene_name": "diffusion"}]
        payload = json.dumps([{
            "scene_name": "diffusion",
            "action": "create",
            "summary": "new scene",
            "added_fact_ids": [61],
            "content": "diffusion content",
        }])
        extractor = SceneExtractor(FakeL1Repo(new_facts), repo, _fake_llm(payload))

        asyncio.run(extractor.consolidate_incremental(new_facts))

        assert repo.get_by_name("diffusion") is not None
        assert repo.get_fact_ids("diffusion") == [61]

    def test_merged_from_folds_and_deletes_scene(self, tmp_path):
        repo = L2FileRepo(tmp_path)
        repo.upsert(L2Scene(scene_name="attention", summary="a", content_md="a", fact_ids=[1, 2]))
        repo.upsert(L2Scene(scene_name="transformer_old", summary="b", content_md="b", fact_ids=[3, 4]))

        new_facts = [{"fact_id": 71, "fact_type": "episodic", "content": "合并", "scene_name": "attention"}]
        payload = json.dumps([{
            "scene_name": "attention",
            "action": "update",
            "summary": "merged",
            "added_fact_ids": [71],
            "content": "merged content",
            "merged_from": ["transformer_old"],
        }])
        extractor = SceneExtractor(FakeL1Repo(new_facts), repo, _fake_llm(payload))

        asyncio.run(extractor.consolidate_incremental(new_facts))

        # attention 持有原有 [1,2] + transformer_old [3,4] + 新增 [71]
        assert sorted(repo.get_fact_ids("attention")) == [1, 2, 3, 4, 71]
        assert repo.get_by_name("transformer_old") is None

    def test_no_existing_scenes_falls_back_to_full(self, tmp_path):
        repo = L2FileRepo(tmp_path)
        facts = [{"fact_id": 1, "fact_type": "episodic", "content": "唯一事实", "scene_name": "general"}]
        # consolidate() 使用 CONSOLIDATE 格式（含 fact_ids 字段，无 action）
        payload = json.dumps([{
            "scene_name": "general",
            "summary": "s",
            "fact_ids": [1],
            "content": "c",
        }])
        extractor = SceneExtractor(FakeL1Repo(facts), repo, _fake_llm(payload))

        asyncio.run(extractor.consolidate_incremental(facts))

        assert repo.get_by_name("general") is not None
        assert repo.get_fact_ids("general") == [1]

    def test_llm_failure_returns_empty_and_keeps_scenes(self, tmp_path):
        repo = L2FileRepo(tmp_path)
        repo.upsert(L2Scene(scene_name="transformer", summary="s", content_md="c", fact_ids=[1]))

        async def bad_llm(system: str, user: str) -> str:
            raise RuntimeError("llm down")

        new_facts = [{"fact_id": 2, "fact_type": "episodic", "content": "x", "scene_name": "transformer"}]
        extractor = SceneExtractor(FakeL1Repo(new_facts), repo, bad_llm)

        result = asyncio.run(extractor.consolidate_incremental(new_facts))

        assert result == []
        assert repo.get_fact_ids("transformer") == [1]  # 场景保持不变
