"""Build hooks for packaging the generated models.dev database."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent
_SRC = _ROOT / "src"
_DB_PATH = _SRC / "modelsdotdev" / "_db.sqlite"


def _uv_build() -> Any:
    return importlib.import_module("uv_build")


def _ensure_database() -> None:
    if _DB_PATH.is_file():
        return

    src = str(_SRC)
    if src not in sys.path:
        sys.path.insert(0, src)

    sync = importlib.import_module("modelsdotdev._internal.sync")
    sync.generate_database(output=_DB_PATH)


def build_sdist(
    sdist_directory: str,
    config_settings: dict[str, Any] | None = None,
) -> str:
    """Build a source distribution after ensuring package data exists."""
    _ensure_database()
    return _uv_build().build_sdist(sdist_directory, config_settings)


def build_wheel(
    wheel_directory: str,
    config_settings: dict[str, Any] | None = None,
    metadata_directory: str | None = None,
) -> str:
    """Build a wheel after ensuring package data exists."""
    _ensure_database()
    return _uv_build().build_wheel(
        wheel_directory,
        config_settings,
        metadata_directory,
    )


def build_editable(
    wheel_directory: str,
    config_settings: dict[str, Any] | None = None,
    metadata_directory: str | None = None,
) -> str:
    """Build an editable wheel without mutating the source checkout."""
    return _uv_build().build_editable(
        wheel_directory,
        config_settings,
        metadata_directory,
    )


def get_requires_for_build_wheel(
    config_settings: dict[str, Any] | None = None,
) -> list[str]:
    """Return additional build requirements for wheel builds."""
    hook = getattr(_uv_build(), "get_requires_for_build_wheel", None)
    if hook is None:
        return []
    return hook(config_settings)


def get_requires_for_build_editable(
    config_settings: dict[str, Any] | None = None,
) -> list[str]:
    """Return additional build requirements for editable wheel builds."""
    hook = getattr(_uv_build(), "get_requires_for_build_editable", None)
    if hook is None:
        return []
    return hook(config_settings)


def prepare_metadata_for_build_wheel(
    metadata_directory: str,
    config_settings: dict[str, Any] | None = None,
) -> str:
    """Prepare wheel metadata using uv_build."""
    return _uv_build().prepare_metadata_for_build_wheel(
        metadata_directory,
        config_settings,
    )


def prepare_metadata_for_build_editable(
    metadata_directory: str,
    config_settings: dict[str, Any] | None = None,
) -> str:
    """Prepare editable wheel metadata using uv_build."""
    return _uv_build().prepare_metadata_for_build_editable(
        metadata_directory,
        config_settings,
    )
