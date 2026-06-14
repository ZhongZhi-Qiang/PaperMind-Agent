"""Scene index management for L2 scene blocks."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from ..storage.l2_repo import L2Repo

logger = logging.getLogger(__name__)


class SceneIndex:
    """Manages scene_index.json for L2 scene navigation.

    Provides a lightweight index that agents can read to browse scenes
    without loading all scene content.
    """

    def __init__(self, l2_repo: L2Repo, index_path: str | Path = "scene_blocks/index.json"):
        self._l2 = l2_repo
        self._index_path = Path(index_path)

    def rebuild(self) -> dict[str, Any]:
        """Rebuild scene_index.json from database."""
        scenes = self._l2.get_all()
        index = {
            "total_scenes": len(scenes),
            "scenes": [
                {
                    "scene_name": s["scene_name"],
                    "fact_count": len(s.get("fact_ids") or []),
                    "updated_at": s["updated_at"].isoformat() if s.get("updated_at") else None,
                }
                for s in scenes
            ],
        }

        # Write to file
        self._index_path.parent.mkdir(parents=True, exist_ok=True)
        self._index_path.write_text(json.dumps(index, indent=2, ensure_ascii=False), encoding="utf-8")
        logger.info("Scene index rebuilt: %d scenes", len(scenes))
        return index

    def load(self) -> dict[str, Any] | None:
        """Load scene index from file."""
        if not self._index_path.exists():
            return None
        try:
            return json.loads(self._index_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
