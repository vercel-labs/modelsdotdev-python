"""Create a tiny baseline database for approval-gate dry runs.

The dry-run workflow must exercise the same URL checker and protected
environment approval path as the real publish workflow, but it must not depend
on the latest PyPI wheel or any real provider data. This script creates the
minimal SQLite tables the checker reads, with one previously-known provider and
one previously-known model override.
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path


def main() -> int:
    """Write the deterministic dry-run baseline SQLite database."""
    parser = argparse.ArgumentParser(
        description="Create an API URL approval dry-run baseline database.",
    )
    parser.add_argument("output", type=Path)
    args = parser.parse_args()

    output = args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        output.unlink()

    with sqlite3.connect(output) as connection:
        connection.execute(
            "CREATE TABLE providers (id TEXT PRIMARY KEY, api TEXT)",
        )
        connection.execute(
            """
            CREATE TABLE models (
                provider_id TEXT NOT NULL,
                id TEXT NOT NULL,
                provider_api TEXT
            )
            """,
        )
        connection.execute(
            "INSERT INTO providers (id, api) VALUES (?, ?)",
            (
                "fixture-known-provider",
                "https://api.fixture-provider.invalid/v1",
            ),
        )
        connection.execute(
            """
            INSERT INTO models (provider_id, id, provider_api)
            VALUES (?, ?, ?)
            """,
            (
                "fixture-known-provider",
                "fixture-known-model",
                "https://api.fixture-model.invalid/v1",
            ),
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
