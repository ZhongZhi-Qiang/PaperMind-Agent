"""Mermaid graph builder utilities for symbolic context offload.

Simplified from original — LLM-based L2 now handles semantic Mermaid generation.
Only utility functions remain here.
"""

from __future__ import annotations

import hashlib
import re


def generate_ref_id(tool_name: str, content: str) -> str:
    """Generate a deterministic reference ID for a tool result."""
    h = hashlib.sha256(f"{tool_name}:{content[:1000]}".encode()).hexdigest()[:12]
    return f"ref_{h}"


def _sanitize(text: str) -> str:
    """Sanitize text for Mermaid node labels."""
    text = text.replace('"', "'").replace("\n", " ").replace("\r", "")
    text = re.sub(r'[{}\[\]()]', '', text)
    return text.strip()
