"""Tests for HarnessReviewMiddleware."""

import json
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from graph.harness_review import (
    HarnessReviewMiddleware,
    ReviewOutput,
    ToolAudit,
    build_review_prompt,
    REVIEW_SYSTEM_PROMPT,
)


class TestBuildReviewPrompt:
    def test_includes_user_message(self):
        messages = [{"role": "user", "content": "What is Python?"}]
        prompt = build_review_prompt(messages)
        assert "What is Python?" in prompt

    def test_includes_assistant_reply(self):
        messages = [
            {"role": "user", "content": "What is Python?"},
            {"role": "assistant", "content": "Python is a programming language."},
        ]
        prompt = build_review_prompt(messages)
        assert "Python is a programming language" in prompt

    def test_includes_tool_calls(self):
        messages = [
            {"role": "user", "content": "Read the file"},
            {"role": "assistant", "content": "", "tool_calls": [{"name": "read_file", "args": {"path": "test.py"}}]},
            {"role": "tool", "content": "file content here"},
        ]
        prompt = build_review_prompt(messages)
        assert "read_file" in prompt


class TestHarnessReviewMiddleware:
    def test_returns_none_when_disabled(self):
        middleware = HarnessReviewMiddleware()
        mock_settings = MagicMock()
        mock_settings.harness_review_enabled = False
        with patch("config.get_settings", return_value=mock_settings):
            state = {"messages": [MagicMock(), MagicMock()]}
            result = middleware._do_review(state)
        assert result is None

    def test_returns_none_with_fewer_than_2_messages(self):
        middleware = HarnessReviewMiddleware()
        mock_settings = MagicMock()
        mock_settings.harness_review_enabled = True
        with patch("config.get_settings", return_value=mock_settings):
            state = {"messages": [MagicMock()]}
            result = middleware._do_review(state)
        assert result is None

    def test_returns_none_with_empty_messages(self):
        middleware = HarnessReviewMiddleware()
        mock_settings = MagicMock()
        mock_settings.harness_review_enabled = True
        with patch("config.get_settings", return_value=mock_settings):
            state = {"messages": []}
            result = middleware._do_review(state)
        assert result is None

    def test_calls_llm_and_returns_review_on_success(self):
        review_json = json.dumps({
            "quality_score": 9,
            "hallucination_risk": "low",
            "issues": [],
            "tool_audit": {"total_calls": 0, "appropriate": 0, "flagged": 0, "details": []},
            "summary": "Great response",
        })
        mock_response = MagicMock()
        mock_response.content = review_json

        mock_llm = MagicMock()
        mock_llm.invoke.return_value = mock_response

        middleware = HarnessReviewMiddleware(llm=mock_llm)
        mock_settings = MagicMock()
        mock_settings.harness_review_enabled = True

        msg1 = MagicMock()
        msg1.type = "user"
        msg1.content = "Hello"
        msg1.tool_calls = None
        msg2 = MagicMock()
        msg2.type = "assistant"
        msg2.content = "Hi there"
        msg2.tool_calls = None

        with patch("config.get_settings", return_value=mock_settings):
            state = {"messages": [msg1, msg2]}
            result = middleware._do_review(state)

        assert result is not None
        assert result["quality_score"] == 9
        assert result["hallucination_risk"] == "low"
        mock_llm.invoke.assert_called_once()

    def test_returns_none_when_llm_raises(self):
        mock_llm = MagicMock()
        mock_llm.invoke.side_effect = RuntimeError("LLM failure")

        middleware = HarnessReviewMiddleware(llm=mock_llm)
        mock_settings = MagicMock()
        mock_settings.harness_review_enabled = True

        msg1 = MagicMock()
        msg1.type = "user"
        msg1.content = "Hello"
        msg1.tool_calls = None
        msg2 = MagicMock()
        msg2.type = "assistant"
        msg2.content = "Hi"
        msg2.tool_calls = None

        with patch("config.get_settings", return_value=mock_settings):
            state = {"messages": [msg1, msg2]}
            result = middleware._do_review(state)

        assert result is None
