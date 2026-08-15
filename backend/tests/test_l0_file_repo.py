"""Tests for L0 raw-message file storage (data consistency)."""

from memory_module_v3.storage.l0_file_repo import L0FileRepo, L0Message


class TestL0FileRepo:
    def test_insert_returns_incrementing_msg_ids(self, tmp_path):
        repo = L0FileRepo(tmp_path)
        first = repo.insert(L0Message(session_id="s1", role="user", content="hi"))
        second = repo.insert(L0Message(session_id="s1", role="assistant", content="hello"))
        assert first == 0
        assert second == 1

    def test_insert_batch_returns_contiguous_ids(self, tmp_path):
        repo = L0FileRepo(tmp_path)
        ids = repo.insert_batch([
            L0Message(session_id="s1", role="user", content="a"),
            L0Message(session_id="s1", role="assistant", content="b"),
        ])
        assert ids == [0, 1]

    def test_get_by_ids_returns_matching_messages(self, tmp_path):
        repo = L0FileRepo(tmp_path)
        repo.insert_batch([
            L0Message(session_id="s1", role="user", content="a"),
            L0Message(session_id="s1", role="assistant", content="b"),
            L0Message(session_id="s1", role="user", content="c"),
        ])
        msgs = repo.get_by_ids([0, 2], session_id="s1")
        assert [m["content"] for m in msgs] == ["a", "c"]

    def test_get_by_ids_skips_out_of_range_safely(self, tmp_path):
        repo = L0FileRepo(tmp_path)
        repo.insert(L0Message(session_id="s1", role="user", content="a"))
        msgs = repo.get_by_ids([0, 99], session_id="s1")
        assert len(msgs) == 1
        assert msgs[0]["content"] == "a"

    def test_sessions_are_isolated(self, tmp_path):
        repo = L0FileRepo(tmp_path)
        repo.insert(L0Message(session_id="s1", role="user", content="from-s1"))
        repo.insert(L0Message(session_id="s2", role="user", content="from-s2"))
        assert repo.get_by_ids([0], session_id="s1")[0]["content"] == "from-s1"
        assert repo.get_by_ids([0], session_id="s2")[0]["content"] == "from-s2"

    def test_get_by_ids_requires_session(self, tmp_path):
        repo = L0FileRepo(tmp_path)
        repo.insert(L0Message(session_id="s1", role="user", content="a"))
        assert repo.get_by_ids([0]) == []
