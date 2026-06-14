"""Tests for HarnessSecurityMiddleware rule engine."""

import os
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.messages import ToolMessage

from graph.harness_security import (
    BUILTIN_DANGEROUS_COMMANDS,
    BUILTIN_PROTECTED_PATTERNS,
    RulesCache,
    _extract_file_paths,
    check_custom_rules,
    check_dangerous_command,
    check_protected_path,
    load_rules,
    HarnessSecurityMiddleware,
    _get_rules,
)


class TestProtectedPaths:
    def test_blocks_env_file(self):
        assert check_protected_path("/app/.env") is not None

    def test_blocks_env_override(self):
        assert check_protected_path("/app/.env.production") is not None

    def test_blocks_credentials(self):
        assert check_protected_path("/app/credentials.json") is not None

    def test_blocks_key_file(self):
        assert check_protected_path("/app/server.key") is not None

    def test_blocks_pem_file(self):
        assert check_protected_path("/app/cert.pem") is not None

    def test_allows_normal_file(self):
        assert check_protected_path("/app/src/main.py") is None

    def test_allows_config_yaml(self):
        assert check_protected_path("/app/config/settings.yaml") is None


class TestDangerousCommands:
    def test_blocks_rm_rf(self):
        assert check_dangerous_command("rm -rf /") is not None

    def test_blocks_rm_rf_recursive(self):
        assert check_dangerous_command("rm -rf /home/user") is not None

    def test_blocks_bare_rm_rf(self):
        assert check_dangerous_command("rm -rf") is not None

    def test_blocks_drop_table(self):
        assert check_dangerous_command("DROP TABLE users") is not None

    def test_allows_normal_rm(self):
        assert check_dangerous_command("rm file.txt") is None

    def test_allows_ls(self):
        assert check_dangerous_command("ls -la") is None

    def test_blocks_dd_dev_sd(self):
        assert check_dangerous_command("dd if=/dev/zero > /dev/sda") is not None


class TestCustomRules:
    def test_matches_command_contains(self):
        rules = [
            {
                "name": "block shadow",
                "tool": "terminal",
                "condition": {"command_contains": "/etc/shadow"},
                "message": "blocked",
            }
        ]
        result = check_custom_rules("terminal", {"command": "cat /etc/shadow"}, rules)
        assert result is not None

    def test_no_match_returns_none(self):
        rules = [
            {
                "name": "block shadow",
                "tool": "terminal",
                "condition": {"command_contains": "/etc/shadow"},
                "message": "blocked",
            }
        ]
        result = check_custom_rules("terminal", {"command": "ls /home"}, rules)
        assert result is None

    def test_wrong_tool_ignored(self):
        rules = [
            {
                "name": "block shadow",
                "tool": "terminal",
                "condition": {"command_contains": "/etc/shadow"},
                "message": "blocked",
            }
        ]
        result = check_custom_rules("python_repl", {"command": "cat /etc/shadow"}, rules)
        assert result is None

    def test_matches_code_contains(self):
        rules = [
            {
                "name": "block subprocess",
                "tool": "python_repl",
                "condition": {"code_contains": "subprocess.call"},
                "message": "code rule blocked",
            }
        ]
        result = check_custom_rules(
            "python_repl", {"code": "import subprocess; subprocess.call(['ls'])"}, rules
        )
        assert result is not None
        assert "code rule blocked" in result

    def test_code_contains_no_match(self):
        rules = [
            {
                "name": "block subprocess",
                "tool": "python_repl",
                "condition": {"code_contains": "subprocess.call"},
                "message": "code rule blocked",
            }
        ]
        result = check_custom_rules(
            "python_repl", {"code": "print('hello')"}, rules
        )
        assert result is None


class TestLoadRules:
    def test_loads_valid_yaml(self, tmp_path):
        rules_file = tmp_path / "rules.yaml"
        rules_file.write_text(
            "security:\n"
            "  protected_paths:\n"
            '    - "**/.env"\n'
            "  dangerous_commands:\n"
            '    - pattern: "rm\\\\s+-rf"\n'
            '      message: "blocked"\n'
            "  custom_rules: []\n"
        )
        rules = load_rules(str(rules_file))
        assert "protected_paths" in rules
        assert len(rules["protected_paths"]) == 1

    def test_returns_empty_on_missing_file(self):
        rules = load_rules("/nonexistent/path.yaml")
        assert rules["protected_paths"] == []
        assert rules["dangerous_commands"] == []
        assert rules["custom_rules"] == []

    def test_returns_empty_on_malformed_yaml(self, tmp_path):
        rules_file = tmp_path / "bad.yaml"
        rules_file.write_text("{{{{invalid yaml: [}}}}")
        rules = load_rules(str(rules_file))
        assert rules["protected_paths"] == []
        assert rules["dangerous_commands"] == []
        assert rules["custom_rules"] == []


class TestExtractFilePaths:
    def test_extracts_absolute_path(self):
        assert "/app/src/main.py" in _extract_file_paths({"path": "/app/src/main.py"})

    def test_extracts_relative_path(self):
        assert "./config.yaml" in _extract_file_paths({"file": "./config.yaml"})

    def test_extracts_backslash_path(self):
        assert "C:\\Users\\test" in _extract_file_paths({"file_path": "C:\\Users\\test"})

    def test_ignores_url(self):
        """URLs should not be extracted as file paths."""
        result = _extract_file_paths({"path": "https://evil.com/script"})
        assert result == []

    def test_ignores_plain_name(self):
        """Plain names without slashes or dot-prefix should be ignored."""
        result = _extract_file_paths({"filename": "main.py"})
        assert result == []

    def test_extracts_dot_prefixed(self):
        assert ".env" in _extract_file_paths({"file": ".env"})


class TestWrapToolCall:
    """Tests for HarnessSecurityMiddleware.wrap_tool_call."""

    def _make_request(self, name="terminal", args=None, req_id="call-1"):
        return SimpleNamespace(name=name, args=args or {}, id=req_id)

    def test_intercepts_protected_path(self):
        mw = HarnessSecurityMiddleware()
        request = self._make_request(args={"path": "/app/.env"})
        handler = MagicMock()
        result = mw.wrap_tool_call(request, handler)
        assert isinstance(result, ToolMessage)
        assert "Harness" in result.content
        handler.assert_not_called()

    def test_intercepts_dangerous_command(self):
        mw = HarnessSecurityMiddleware()
        request = self._make_request(args={"command": "rm -rf /"})
        handler = MagicMock()
        result = mw.wrap_tool_call(request, handler)
        assert isinstance(result, ToolMessage)
        assert "Harness" in result.content
        handler.assert_not_called()

    def test_intercepts_custom_rule(self):
        fake_cache = RulesCache(
            custom_rules=[
                {
                    "name": "block wget",
                    "tool": "terminal",
                    "condition": {"command_contains": "wget"},
                    "message": "wget blocked",
                }
            ],
            file_path="fake",
            mtime=1.0,
        )
        with patch("graph.harness_security._get_rules", return_value=fake_cache):
            mw = HarnessSecurityMiddleware()
            request = self._make_request(args={"command": "wget http://evil.com"})
            handler = MagicMock()
            result = mw.wrap_tool_call(request, handler)
            assert isinstance(result, ToolMessage)
            assert "wget blocked" in result.content
            handler.assert_not_called()

    def test_allows_safe_call_passes_to_handler(self):
        mw = HarnessSecurityMiddleware()
        request = self._make_request(args={"command": "ls -la"})
        handler = MagicMock(return_value="ok")
        result = mw.wrap_tool_call(request, handler)
        handler.assert_called_once_with(request)
        assert result == "ok"

    def test_parses_json_string_args(self):
        """When args is a JSON string, it should be parsed and checked."""
        import json

        mw = HarnessSecurityMiddleware()
        request = self._make_request(args=json.dumps({"command": "rm -rf /"}))
        handler = MagicMock()
        result = mw.wrap_tool_call(request, handler)
        assert isinstance(result, ToolMessage)
        assert "Harness" in result.content
        handler.assert_not_called()


class TestGetRulesHotReload:
    """Tests for _get_rules hot-reload on mtime change."""

    def test_reloads_when_mtime_changes(self, tmp_path):
        import graph.harness_security as hsm

        rules_file = tmp_path / "rules.yaml"
        rules_file.write_text(
            "security:\n"
            "  protected_paths:\n"
            '    - "**/.env"\n'
            "  dangerous_commands: []\n"
            "  custom_rules: []\n"
        )

        fake_settings = SimpleNamespace(
            backend_dir=tmp_path,
            harness_rules_path="rules.yaml",
        )

        old_cache = hsm._rules_cache
        try:
            hsm._rules_cache = RulesCache(file_path="", mtime=0.0)
            with patch("config.get_settings", return_value=fake_settings), \
                 patch("os.path.getmtime", return_value=1000.0):
                cache1 = _get_rules()
                assert cache1.mtime == 1000.0

                # Simulate mtime change by returning a different value
                with patch("os.path.getmtime", return_value=2000.0):
                    rules_file.write_text(
                        "security:\n"
                        "  protected_paths:\n"
                        '    - "**/.env"\n'
                        '    - "**/id_rsa"\n'
                        "  dangerous_commands: []\n"
                        "  custom_rules: []\n"
                    )
                    cache2 = _get_rules()
                    assert cache2.mtime == 2000.0
                    assert len(cache2.protected_paths) == 2
        finally:
            hsm._rules_cache = old_cache
