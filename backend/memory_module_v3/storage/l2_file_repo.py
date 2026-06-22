"""L2 scene storage: markdown files + JSON index."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class L2Scene:
    scene_name: str = ""
    content_md: str = ""
    fact_ids: list[int] = field(default_factory=list)
    updated_at: datetime | None = None


class L2FileRepo:
    """File-based L2 scene storage.

    Each scene is a markdown file at {scenes_dir}/{scene_name}.md.
    A JSON index at {scenes_dir}/_index.json provides lightweight navigation.
    """

    def __init__(self, scenes_dir: str | Path):
        self._dir = Path(scenes_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._index_path = self._dir / "_index.json"

    def upsert(self, scene: L2Scene) -> None:
        """Write scene markdown file and update index."""
        if not scene.scene_name:
            return

        now = datetime.now(timezone.utc).isoformat()
        scene.updated_at = datetime.now(timezone.utc)

        # Write scene markdown
        file_path = self._dir / f"{scene.scene_name}.md"
        content = self._format_scene(scene)
        file_path.write_text(content, encoding="utf-8")

        # Update index
        index = self._read_index()
        entry = {
            "scene_name": scene.scene_name,
            "file": f"{scene.scene_name}.md",
            "fact_count": len(scene.fact_ids),
            "updated_at": now,
        }
        # Upsert entry in index
        found = False
        for i, item in enumerate(index):
            if item["scene_name"] == scene.scene_name:
                index[i] = entry
                found = True
                break
        if not found:
            index.append(entry)
        self._write_index(index)

        logger.debug("L2 scene saved: %s", scene.scene_name)

    def get_all(self) -> list[dict[str, Any]]:
        """Return lightweight navigation list from index."""
        return self._read_index()

    def get_by_name(self, scene_name: str) -> str | None:
        """Read full scene content by name."""
        file_path = self._dir / f"{scene_name}.md"
        if file_path.exists():
            return file_path.read_text(encoding="utf-8")
        return None

    def list_names(self) -> list[str]:
        """Return sorted scene names."""
        return sorted(item["scene_name"] for item in self._read_index())

    def delete_by_name(self, scene_name: str) -> bool:
        """Delete scene file and remove from index."""
        file_path = self._dir / f"{scene_name}.md"
        deleted = False
        if file_path.exists():
            file_path.unlink()
            deleted = True

        index = self._read_index()
        index = [item for item in index if item["scene_name"] != scene_name]
        self._write_index(index)
        return deleted

    def _read_index(self) -> list[dict[str, Any]]:
        """Read scene index JSON."""
        if not self._index_path.exists():
            return []
        try:
            return json.loads(self._index_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []

    def _write_index(self, index: list[dict[str, Any]]) -> None:
        """Write scene index JSON."""
        self._index_path.write_text(
            json.dumps(index, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _format_scene(self, scene: L2Scene) -> str:
        """Format scene as markdown with YAML frontmatter."""
        now = scene.updated_at or datetime.now(timezone.utc)
        fact_ids_str = json.dumps(scene.fact_ids)
        return (
            f"---\n"
            f"scene_name: {scene.scene_name}\n"
            f"fact_ids: {fact_ids_str}\n"
            f"updated_at: {now.isoformat()}\n"
            f"---\n\n"
            f"{scene.content_md}\n"
        )
