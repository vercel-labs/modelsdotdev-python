"""Hatch build hook for packaging the generated models.dev database."""

from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path
from typing import Any

from hatchling.builders.hooks.plugin.interface import BuildHookInterface

DB_ENV = "MODELDOTDEV_BUILD_SOURCE"
API_URL = "https://models.dev/api.json"


class DatabaseBuildHook(BuildHookInterface[Any]):
    """Generate the bundled SQLite database before distribution builds."""

    def initialize(self, version: str, build_data: dict[str, Any]) -> None:
        """Run before Hatchling builds each distribution target."""
        if version == "editable":
            return

        root = Path(self.root)
        db_path = root / "src" / "modelsdotdev" / "_db.sqlite"
        source = os.environ.get(DB_ENV, API_URL)
        source_path = _local_source_path(root, source)

        if source_path is None:
            if db_path.is_file():
                _include_database(self.target_name, build_data, db_path)
                return
        else:
            source = str(source_path)

        src = str(root / "src")
        if src not in sys.path:
            sys.path.insert(0, src)

        sync = importlib.import_module("modelsdotdev._internal.sync")
        sync.generate_database(source=source, output=db_path)
        _include_database(self.target_name, build_data, db_path)


def _local_source_path(root: Path, source: str) -> Path | None:
    if source.startswith(("http://", "https://")):
        return None

    path = Path(source).expanduser()
    if not path.is_absolute():
        path = root / path
    if path.is_file():
        return path
    return None


def _include_database(
    target_name: str,
    build_data: dict[str, Any],
    db_path: Path,
) -> None:
    target_path = "modelsdotdev/_db.sqlite"
    if target_name == "sdist":
        target_path = "src/modelsdotdev/_db.sqlite"

    force_include = build_data.setdefault("force_include", {})
    force_include[str(db_path)] = target_path


def get_build_hook() -> type[DatabaseBuildHook]:
    """Return the custom Hatchling build hook class."""
    return DatabaseBuildHook
