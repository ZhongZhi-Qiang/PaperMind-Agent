"""L3 persona storage: single markdown file."""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class L3FileRepo:
    """File-based L3 persona storage.

    Persona is stored as a single markdown file at {data_dir}/persona.md.
    """

    def __init__(self, data_dir: str | Path):
        self._path = Path(data_dir) / "persona.md"

    def get(self) -> str | None:
        """Read persona content from file."""
        if self._path.exists():
            return self._path.read_text(encoding="utf-8")
        return None

    def set(self, content: str) -> None:
        """Write persona content to file."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(content, encoding="utf-8")
        logger.debug("L3 persona saved (%d chars)", len(content))
