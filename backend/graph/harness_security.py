"""Harness Security Middleware: intercept tool calls to block dangerous operations.

Implements wrap_tool_call to check:
1. Sensitive file access (glob patterns)
2. Dangerous commands (regex)
3. Custom rules from YAML config
"""

from __future__ import annotations

import fnmatch
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any

import yaml
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import AgentState, ContextT, ResponseT
from langchain_core.messages import ToolMessage
from typing_extensions import override

logger = logging.getLogger(__name__)

# Built-in protected file patterns
BUILTIN_PROTECTED_PATTERNS = [
    "**/.env",
    "**/.env.*",
    "**/credentials*",
    "**/secrets*",
    "**/*.key",
    "**/*.pem",
    "**/.git/config",
    "**/.ssh/*",
]

# Built-in dangerous command patterns (regex, message)
BUILTIN_DANGEROUS_COMMANDS = [
    (r"rm\s+-rf", "禁止递归删除"),
    (r"DROP\s+TABLE", "禁止删除数据库表"),
    (r">\s*/dev/sd", "禁止直接写入磁盘设备"),
]


@dataclass
class RulesCache:
    """Cached rules with mtime for hot-reload."""

    protected_paths: list[str] = field(default_factory=list)
    dangerous_commands: list[tuple[str, str]] = field(default_factory=list)
    custom_rules: list[dict[str, Any]] = field(default_factory=list)
    mtime: float = 0.0
    file_path: str = ""


_rules_cache = RulesCache()


def _extract_file_paths(args: dict[str, Any]) -> list[str]:
    """Extract file paths from tool arguments."""
    paths = []
    for key in ("path", "file_path", "filename", "file"):
        val = args.get(key)
        if isinstance(val, str) and ("/" in val or "\\" in val or val.startswith(".")):
            # Skip URLs — they look like paths but are not local files
            if val.startswith(("http://", "https://", "ftp://")):
                continue
            paths.append(val)
    return paths


def _extract_command(args: dict[str, Any]) -> str:
    """Extract command string from tool arguments."""
    for key in ("command", "code", "input"):
        val = args.get(key)
        if isinstance(val, str):
            return val
    return ""


def check_protected_path(file_path: str) -> str | None:
    """Check if a file path matches any protected pattern. Returns message or None."""
    rules = _get_rules()
    patterns = rules.protected_paths or BUILTIN_PROTECTED_PATTERNS
    for pattern in patterns:
        if fnmatch.fnmatch(file_path, pattern) or fnmatch.fnmatch(
            os.path.basename(file_path), pattern
        ):
            return f"访问受保护文件被拦截: {file_path} (匹配规则: {pattern})"
    return None


def check_dangerous_command(command: str) -> str | None:
    """Check if a command matches any dangerous pattern. Returns message or None."""
    rules = _get_rules()
    patterns = rules.dangerous_commands or BUILTIN_DANGEROUS_COMMANDS
    for pattern, message in patterns:
        if re.search(pattern, command, re.IGNORECASE):
            return message
    return None


def check_custom_rules(
    tool_name: str, args: dict[str, Any], rules: list[dict[str, Any]]
) -> str | None:
    """Check tool call against custom rules. Returns message or None."""
    command = _extract_command(args)
    for rule in rules:
        rule_tool = rule.get("tool")
        if rule_tool and rule_tool != tool_name:
            continue
        condition = rule.get("condition", {})
        if "command_contains" in condition and condition["command_contains"] in command:
            return rule.get("message", "自定义规则拦截")
        if "code_contains" in condition and condition["code_contains"] in command:
            return rule.get("message", "自定义规则拦截")
    return None


def load_rules(file_path: str) -> dict[str, Any]:
    """Load rules from YAML file.

    Returns dict with protected_paths, dangerous_commands, custom_rules.
    """
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        security = data.get("security", {})
        return {
            "protected_paths": security.get("protected_paths", []),
            "dangerous_commands": [
                (item["pattern"], item.get("message", "危险命令"))
                for item in security.get("dangerous_commands", [])
            ],
            "custom_rules": security.get("custom_rules", []),
        }
    except Exception as e:
        logger.warning("Failed to load harness rules from %s: %s", file_path, e)
        return {"protected_paths": [], "dangerous_commands": [], "custom_rules": []}


def _get_rules() -> RulesCache:
    """Get rules with hot-reload based on file mtime."""
    global _rules_cache
    from config import get_settings

    settings = get_settings()
    file_path = str(settings.backend_dir / settings.harness_rules_path)

    try:
        current_mtime = os.path.getmtime(file_path)
    except OSError:
        if _rules_cache.protected_paths or _rules_cache.dangerous_commands:
            return _rules_cache
        return RulesCache(
            protected_paths=BUILTIN_PROTECTED_PATTERNS,
            dangerous_commands=BUILTIN_DANGEROUS_COMMANDS,
        )

    if _rules_cache.file_path == file_path and _rules_cache.mtime == current_mtime:
        return _rules_cache

    loaded = load_rules(file_path)
    _rules_cache = RulesCache(
        protected_paths=loaded["protected_paths"] or BUILTIN_PROTECTED_PATTERNS,
        dangerous_commands=loaded["dangerous_commands"] or BUILTIN_DANGEROUS_COMMANDS,
        custom_rules=loaded["custom_rules"],
        mtime=current_mtime,
        file_path=file_path,
    )
    logger.info("Harness rules reloaded from %s", file_path)
    return _rules_cache


class HarnessSecurityMiddleware(
    AgentMiddleware[AgentState[ResponseT], ContextT, ResponseT]
):
    """Intercepts tool calls to enforce security rules."""

    def __init__(self) -> None:
        super().__init__()

    @override
    def wrap_tool_call(self, request: Any, handler: Any) -> Any:
        tool_name = getattr(request, "name", "unknown")
        args = getattr(request, "args", {})
        if isinstance(args, str):
            import json

            try:
                args = json.loads(args)
            except (json.JSONDecodeError, TypeError):
                args = {}

        # Check protected file paths
        for path in _extract_file_paths(args):
            msg = check_protected_path(path)
            if msg:
                logger.warning(
                    "Harness security blocked tool %s: %s", tool_name, msg
                )
                return ToolMessage(
                    content=f"[Harness 安全拦截] {msg}",
                    tool_call_id=str(getattr(request, "id", "")),
                )

        # Check dangerous commands
        command = _extract_command(args)
        if command:
            msg = check_dangerous_command(command)
            if msg:
                logger.warning(
                    "Harness security blocked tool %s: %s", tool_name, msg
                )
                return ToolMessage(
                    content=f"[Harness 安全拦截] {msg}",
                    tool_call_id=str(getattr(request, "id", "")),
                )

        # Check custom rules
        rules = _get_rules()
        if rules.custom_rules:
            msg = check_custom_rules(tool_name, args, rules.custom_rules)
            if msg:
                logger.warning(
                    "Harness security blocked tool %s: %s", tool_name, msg
                )
                return ToolMessage(
                    content=f"[Harness 安全拦截] {msg}",
                    tool_call_id=str(getattr(request, "id", "")),
                )

        return handler(request)

    @override
    async def awrap_tool_call(self, request: Any, handler: Any) -> Any:
        tool_name = getattr(request, "name", "unknown")
        args = getattr(request, "args", {})
        if isinstance(args, str):
            import json

            try:
                args = json.loads(args)
            except (json.JSONDecodeError, TypeError):
                args = {}

        # Check protected file paths
        for path in _extract_file_paths(args):
            msg = check_protected_path(path)
            if msg:
                logger.warning(
                    "Harness security blocked tool %s: %s", tool_name, msg
                )
                return ToolMessage(
                    content=f"[Harness 安全拦截] {msg}",
                    tool_call_id=str(getattr(request, "id", "")),
                )

        # Check dangerous commands
        command = _extract_command(args)
        if command:
            msg = check_dangerous_command(command)
            if msg:
                logger.warning(
                    "Harness security blocked tool %s: %s", tool_name, msg
                )
                return ToolMessage(
                    content=f"[Harness 安全拦截] {msg}",
                    tool_call_id=str(getattr(request, "id", "")),
                )

        # Check custom rules
        rules = _get_rules()
        if rules.custom_rules:
            msg = check_custom_rules(tool_name, args, rules.custom_rules)
            if msg:
                logger.warning(
                    "Harness security blocked tool %s: %s", tool_name, msg
                )
                return ToolMessage(
                    content=f"[Harness 安全拦截] {msg}",
                    tool_call_id=str(getattr(request, "id", "")),
                )

        return await handler(request)


def build_harness_security_middleware() -> HarnessSecurityMiddleware:
    """Factory for HarnessSecurityMiddleware."""
    return HarnessSecurityMiddleware()
