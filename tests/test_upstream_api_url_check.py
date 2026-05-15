from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from typing import TYPE_CHECKING, cast

from modelsdotdev._internal.dist import PROJECT_ROOT

if TYPE_CHECKING:
    from pathlib import Path

CHECK_SCRIPT = (
    PROJECT_ROOT / ".github" / "scripts" / "check_upstream_api_urls.py"
)


def test_provider_url_change_requires_approval(tmp_path: Path) -> None:
    baseline_db = _write_baseline_db(
        tmp_path,
        providers={"known": "https://old.example/v1"},
    )
    current_json = _write_current_json(
        tmp_path,
        {
            "known": _provider(
                api="https://new.example/v1",
            ),
        },
    )

    payload, markdown = _run_check(tmp_path, baseline_db, current_json)

    assert payload["approval_required"] is True
    assert payload["change_count"] == 1
    assert _changes(payload) == [
        {
            "type": "Provider",
            "provider": "known",
            "model": None,
            "previous_url": "https://old.example/v1",
            "current_url": "https://new.example/v1",
        },
    ]
    assert "Manual approval is required" in markdown
    assert "<code>https://old.example/v1</code>" in markdown
    assert "<code>https://new.example/v1</code>" in markdown


def test_provider_url_add_and_remove_require_approval(tmp_path: Path) -> None:
    baseline_db = _write_baseline_db(
        tmp_path,
        providers={
            "added": None,
            "removed": "https://old.example/v1",
        },
    )
    current_json = _write_current_json(
        tmp_path,
        {
            "added": _provider(api="https://new.example/v1"),
            "removed": _provider(),
        },
    )

    payload, _markdown = _run_check(tmp_path, baseline_db, current_json)

    assert payload["approval_required"] is True
    assert _changes(payload) == [
        {
            "type": "Provider",
            "provider": "added",
            "model": None,
            "previous_url": None,
            "current_url": "https://new.example/v1",
        },
        {
            "type": "Provider",
            "provider": "removed",
            "model": None,
            "previous_url": "https://old.example/v1",
            "current_url": None,
        },
    ]


def test_model_override_url_changes_require_approval(tmp_path: Path) -> None:
    baseline_db = _write_baseline_db(
        tmp_path,
        providers={"known": None},
        model_urls={
            ("known", "changed"): "https://old.example/v1",
            ("known", "removed"): "https://removed.example/v1",
        },
    )
    current_json = _write_current_json(
        tmp_path,
        {
            "known": _provider(
                models={
                    "added": _model(api="https://added.example/v1"),
                    "changed": _model(api="https://new.example/v1"),
                    "removed": _model(),
                },
            ),
        },
    )

    payload, markdown = _run_check(tmp_path, baseline_db, current_json)

    assert payload["approval_required"] is True
    assert _changes(payload) == [
        {
            "type": "Model override",
            "provider": "known",
            "model": "added",
            "previous_url": None,
            "current_url": "https://added.example/v1",
        },
        {
            "type": "Model override",
            "provider": "known",
            "model": "changed",
            "previous_url": "https://old.example/v1",
            "current_url": "https://new.example/v1",
        },
        {
            "type": "Model override",
            "provider": "known",
            "model": "removed",
            "previous_url": "https://removed.example/v1",
            "current_url": None,
        },
    ]
    assert "<code>added</code>" in markdown
    assert "<code>changed</code>" in markdown
    assert "<code>removed</code>" in markdown


def test_new_provider_urls_do_not_require_approval(tmp_path: Path) -> None:
    baseline_db = _write_baseline_db(
        tmp_path,
        providers={"known": "https://known.example/v1"},
    )
    current_json = _write_current_json(
        tmp_path,
        {
            "known": _provider(api="https://known.example/v1"),
            "new": _provider(
                api="https://new-provider.example/v1",
                models={
                    "new-model": _model(
                        api="https://new-model.example/v1",
                    ),
                },
            ),
        },
    )

    payload, markdown = _run_check(tmp_path, baseline_db, current_json)

    assert payload["approval_required"] is False
    assert payload["change_count"] == 0
    assert _changes(payload) == []
    assert "No protected upstream API URL changes" in markdown


def _write_baseline_db(
    tmp_path: Path,
    *,
    providers: dict[str, str | None],
    model_urls: dict[tuple[str, str], str] | None = None,
) -> Path:
    path = tmp_path / "baseline.sqlite"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE providers (id TEXT PRIMARY KEY, api TEXT)"
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
        connection.executemany(
            "INSERT INTO providers (id, api) VALUES (?, ?)",
            sorted(providers.items()),
        )
        connection.executemany(
            "INSERT INTO models (provider_id, id, provider_api) "
            "VALUES (?, ?, ?)",
            [
                (provider_id, model_id, api)
                for (provider_id, model_id), api in sorted(
                    (model_urls or {}).items(),
                )
            ],
        )
    return path


def _write_current_json(
    tmp_path: Path,
    data: dict[str, object],
) -> Path:
    path = tmp_path / "current.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _provider(
    *,
    api: str | None = None,
    models: dict[str, object] | None = None,
) -> dict[str, object]:
    provider: dict[str, object] = {"models": models or {}}
    if api is not None:
        provider["api"] = api
    return provider


def _model(*, api: str | None = None) -> dict[str, object]:
    model: dict[str, object] = {}
    if api is not None:
        model["provider"] = {"api": api}
    return model


def _run_check(
    tmp_path: Path,
    baseline_db: Path,
    current_json: Path,
) -> tuple[dict[str, object], str]:
    json_output = tmp_path / "api-url-diff.json"
    markdown_output = tmp_path / "api-url-diff.md"
    subprocess.run(
        [
            sys.executable,
            str(CHECK_SCRIPT),
            str(current_json),
            "--baseline-db",
            str(baseline_db),
            "--baseline-version",
            "0.20260515.1",
            "--digest",
            "abc123",
            "--json-output",
            str(json_output),
            "--markdown-output",
            str(markdown_output),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return (
        cast("dict[str, object]", json.loads(json_output.read_text())),
        markdown_output.read_text(encoding="utf-8"),
    )


def _changes(payload: dict[str, object]) -> list[dict[str, object]]:
    return cast("list[dict[str, object]]", payload["changes"])
