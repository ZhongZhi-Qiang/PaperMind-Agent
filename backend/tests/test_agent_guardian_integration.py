"""Agent + Guardian middleware-order integration tests.

Runs against the real `graph.agent_factory`, stubbing only `create_agent` and
`SummarizationMiddleware` via monkeypatch. The previous version injected fake
modules into `sys.modules` at import time, which leaked into every other test
in the suite (pytest 9 collects test files alphabetically, so this file's fake
`langchain.agents.middleware` shadowed the real one for all later imports).
"""

from __future__ import annotations

from typing import Any

import pytest

from graph.agent_factory import AgentConfig, create_agent_from_config


def _base_config(**overrides: Any) -> AgentConfig:
    defaults: dict[str, Any] = {
        "llm": object(),
        "tools": [],
        "system_prompt": "",
        # Disable security/review/resilience middlewares so these tests focus on the
        # guardian-before-summarization ordering.
        "harness_security_enabled": False,
        "harness_review_enabled": False,
        "resilience_enabled": False,
    }
    defaults.update(overrides)
    return AgentConfig(**defaults)


class TestAgentGuardianIntegration:
    def test_guardian_middleware_before_summarization_when_both_enabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict[str, Any] = {}

        class _FakeSummarizationMiddleware:
            def __init__(self, **_kwargs):
                pass

        def _fake_create_agent(**kwargs):
            captured.update(kwargs)
            return object()

        monkeypatch.setattr("graph.agent_factory.SummarizationMiddleware", _FakeSummarizationMiddleware)
        monkeypatch.setattr("graph.agent_factory.create_agent", _fake_create_agent)

        create_agent_from_config(_base_config(guardian_enabled=True, use_summarization=True))

        middleware = list(captured["middleware"])
        assert len(middleware) == 2
        assert middleware[0].__class__.__name__ == "GuardianMiddleware"
        assert middleware[1].__class__.__name__ == "_FakeSummarizationMiddleware"

    def test_only_guardian_middleware_when_summarization_disabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict[str, Any] = {}

        def _fake_create_agent(**kwargs):
            captured.update(kwargs)
            return object()

        monkeypatch.setattr("graph.agent_factory.create_agent", _fake_create_agent)

        create_agent_from_config(_base_config(guardian_enabled=True, use_summarization=False))

        middleware = list(captured["middleware"])
        assert len(middleware) == 1
        assert middleware[0].__class__.__name__ == "GuardianMiddleware"

    def test_no_guardian_middleware_when_disabled_and_no_summarization(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict[str, Any] = {}

        def _fake_create_agent(**kwargs):
            captured.update(kwargs)
            return object()

        monkeypatch.setattr("graph.agent_factory.create_agent", _fake_create_agent)

        create_agent_from_config(_base_config(guardian_enabled=False, use_summarization=False))

        assert captured["middleware"] == ()

    def test_only_summarization_when_guardian_disabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict[str, Any] = {}

        class _FakeSummarizationMiddleware:
            def __init__(self, **_kwargs):
                pass

        def _fake_create_agent(**kwargs):
            captured.update(kwargs)
            return object()

        monkeypatch.setattr("graph.agent_factory.SummarizationMiddleware", _FakeSummarizationMiddleware)
        monkeypatch.setattr("graph.agent_factory.create_agent", _fake_create_agent)

        create_agent_from_config(_base_config(guardian_enabled=False, use_summarization=True))

        middleware = list(captured["middleware"])
        assert len(middleware) == 1
        assert middleware[0].__class__.__name__ == "_FakeSummarizationMiddleware"

    def test_resilience_middleware_added_when_enabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict[str, Any] = {}

        def _fake_create_agent(**kwargs):
            captured.update(kwargs)
            return object()

        monkeypatch.setattr("graph.agent_factory.create_agent", _fake_create_agent)

        create_agent_from_config(_base_config(
            guardian_enabled=False, use_summarization=False, resilience_enabled=True,
        ))

        names = [m.__class__.__name__ for m in captured["middleware"]]
        assert "ModelRetryMiddleware" in names
        assert "ToolCallLimitMiddleware" in names

    def test_resilience_middleware_skipped_when_disabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict[str, Any] = {}

        def _fake_create_agent(**kwargs):
            captured.update(kwargs)
            return object()

        monkeypatch.setattr("graph.agent_factory.create_agent", _fake_create_agent)

        create_agent_from_config(_base_config(guardian_enabled=False, use_summarization=False))

        assert list(captured["middleware"]) == []
