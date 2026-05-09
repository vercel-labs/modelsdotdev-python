from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from modelsdotdev._internal.data import DATABASE_PATH_ENV
from modelsdotdev._internal.dist import is_project_editable
from modelsdotdev._internal.sync import generate_database

if TYPE_CHECKING:
    import pytest

_TEMP_DB_DIR: tempfile.TemporaryDirectory[str] | None = None


def pytest_sessionstart(session: pytest.Session) -> None:
    root = session.config.rootpath
    if is_project_editable():
        output = root / "src" / "modelsdotdev" / "_db.sqlite"
    else:
        global _TEMP_DB_DIR  # noqa: PLW0603
        _TEMP_DB_DIR = tempfile.TemporaryDirectory(
            prefix="modelsdotdev-tests-",
        )
        output = Path(_TEMP_DB_DIR.name) / "_db.sqlite"
        os.environ[DATABASE_PATH_ENV] = str(output)

    generate_database(output=output)


def pytest_sessionfinish(
    session: pytest.Session,
    exitstatus: int,
) -> None:
    if _TEMP_DB_DIR is not None:
        _TEMP_DB_DIR.cleanup()
