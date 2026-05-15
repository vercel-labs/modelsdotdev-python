"""Gate PyPI publishes when upstream changes API URL routing.

modelsdotdev publishes a bundled snapshot of models.dev data. If upstream is
compromised between releases, a malicious data-only change could redirect a
previously-known provider or model override to an attacker-controlled API URL
without changing this repository. This script compares the fetched upstream JSON
against the latest published wheel and emits a GitHub Actions approval signal
plus a reviewer-friendly Markdown diff.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import sqlite3
import sys
import tempfile
import urllib.error
import urllib.request
import uuid
import zipfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any

PACKAGE_NAME = "modelsdotdev"
PYPI_JSON_URL = f"https://pypi.org/pypi/{PACKAGE_NAME}/json"
WHEEL_DB_PATH = "modelsdotdev/_db.sqlite"


@dataclass(frozen=True, slots=True)
class ApiUrlState:
    """Provider and model API URL state extracted from one data source."""

    provider_ids: frozenset[str]
    provider_urls: dict[str, str | None]
    model_urls: dict[tuple[str, str], str]


@dataclass(frozen=True, slots=True)
class Baseline:
    """Previously published package version and its extracted URL state."""

    version: str
    state: ApiUrlState


@dataclass(frozen=True, slots=True)
class ApiUrlChange:
    """One protected API URL difference requiring reviewer attention."""

    kind: str
    provider_id: str
    model_id: str | None
    previous_url: str | None
    current_url: str | None


class BaselineUnavailableError(RuntimeError):
    """Raised when the previous published package cannot be inspected."""


def main() -> int:
    """Run the API URL gate and write GitHub Actions outputs."""
    parser = argparse.ArgumentParser(
        description="Detect protected upstream API URL changes.",
    )
    parser.add_argument("current_json", type=Path)
    parser.add_argument("--baseline-db", type=Path)
    parser.add_argument("--baseline-version", default="")
    parser.add_argument("--package-name", default=PACKAGE_NAME)
    parser.add_argument("--digest", default="")
    parser.add_argument(
        "--markdown-output",
        type=Path,
        default=Path(".tmp/api-url-diff.md"),
    )
    parser.add_argument(
        "--json-output",
        type=Path,
        default=Path(".tmp/api-url-diff.json"),
    )
    args = parser.parse_args()

    current_state = load_current_state(args.current_json)
    fail_closed_reason = ""
    try:
        if args.baseline_db is None:
            baseline = load_baseline_from_pypi(args.package_name)
        else:
            baseline = load_baseline_from_db(
                args.baseline_db,
                args.baseline_version or "local baseline",
            )
        changes = compare_api_urls(baseline.state, current_state)
    except BaselineUnavailableError as error:
        # If we cannot inspect the previous release, require a human review
        # rather than silently treating the publish as safe.
        baseline = Baseline(version="unknown", state=_empty_state())
        changes = []
        fail_closed_reason = str(error)

    approval_required = bool(changes) or bool(fail_closed_reason)
    summary = _summary(changes, fail_closed_reason)
    markdown = render_markdown(
        changes=changes,
        baseline_version=baseline.version,
        digest=args.digest,
        fail_closed_reason=fail_closed_reason,
    )
    payload = {
        "approval_required": approval_required,
        "baseline_version": baseline.version,
        "change_count": len(changes),
        "changes": [_change_payload(change) for change in changes],
        "digest": args.digest,
        "fail_closed": bool(fail_closed_reason),
        "error": fail_closed_reason or None,
        "summary": summary,
    }

    args.markdown_output.parent.mkdir(parents=True, exist_ok=True)
    args.markdown_output.write_text(markdown, encoding="utf-8")
    args.json_output.parent.mkdir(parents=True, exist_ok=True)
    args.json_output.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    _write_github_outputs(
        {
            "approval_required": str(approval_required).lower(),
            "baseline_version": baseline.version,
            "change_count": str(len(changes)),
            "fail_closed": str(bool(fail_closed_reason)).lower(),
            "json_path": str(args.json_output),
            "markdown_path": str(args.markdown_output),
            "summary": summary,
        },
    )
    sys.stdout.write(f"{summary}\n")
    return 0


def load_current_state(path: Path) -> ApiUrlState:
    """Load provider and model override URLs from fetched upstream JSON."""
    raw_data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw_data, dict):
        raise TypeError("upstream JSON root must be an object")

    provider_urls: dict[str, str | None] = {}
    model_urls: dict[tuple[str, str], str] = {}
    for provider_id, raw_provider in raw_data.items():
        if not isinstance(provider_id, str):
            raise TypeError("upstream JSON provider keys must be strings")
        provider = _object(raw_provider, f"provider {provider_id}")
        provider_urls[provider_id] = _optional_string(
            provider.get("api"),
            f"provider {provider_id}.api",
        )
        models = _object(
            provider.get("models", {}),
            f"provider {provider_id}.models",
        )
        for model_id, raw_model in models.items():
            if not isinstance(model_id, str):
                raise TypeError(
                    f"provider {provider_id}.models keys must be strings",
                )
            model = _object(raw_model, f"model {provider_id}/{model_id}")
            raw_provider_override = model.get("provider")
            if raw_provider_override is None:
                continue
            provider_override = _object(
                raw_provider_override,
                f"model {provider_id}/{model_id}.provider",
            )
            api_url = _optional_string(
                provider_override.get("api"),
                f"model {provider_id}/{model_id}.provider.api",
            )
            if api_url is not None:
                model_urls[provider_id, model_id] = api_url

    return ApiUrlState(
        provider_ids=frozenset(provider_urls),
        provider_urls=provider_urls,
        model_urls=model_urls,
    )


def load_baseline_from_pypi(package_name: str) -> Baseline:
    """Download the latest PyPI wheel and load its bundled database URLs."""
    pypi_json_url = f"https://pypi.org/pypi/{package_name}/json"
    try:
        with urllib.request.urlopen(
            _request(pypi_json_url),
            timeout=60,
        ) as response:
            pypi_data = json.loads(response.read())
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return Baseline(version="none", state=_empty_state())
        raise BaselineUnavailableError(
            f"unable to inspect PyPI release metadata: HTTP {error.code}",
        ) from error
    except (OSError, json.JSONDecodeError) as error:
        raise BaselineUnavailableError(
            f"unable to inspect PyPI release metadata: {error}",
        ) from error

    version = _pypi_version(pypi_data)
    wheel_url = _wheel_url(pypi_data, version)
    try:
        with urllib.request.urlopen(
            _request(wheel_url),
            timeout=60,
        ) as response:
            wheel_payload = response.read()
    except OSError as error:
        raise BaselineUnavailableError(
            f"unable to download baseline wheel for {package_name} {version}: "
            f"{error}",
        ) from error

    try:
        with (
            zipfile.ZipFile(BytesIO(wheel_payload)) as wheel,
            tempfile.TemporaryDirectory() as temporary_dir,
        ):
            db_path = Path(temporary_dir) / "_db.sqlite"
            db_path.write_bytes(wheel.read(WHEEL_DB_PATH))
            return load_baseline_from_db(db_path, version)
    except (KeyError, OSError, sqlite3.Error, zipfile.BadZipFile) as error:
        raise BaselineUnavailableError(
            f"unable to inspect baseline wheel for {package_name} {version}: "
            f"{error}",
        ) from error


def load_baseline_from_db(path: Path, version: str) -> Baseline:
    """Load provider and model override URLs from a bundled SQLite DB."""
    try:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            provider_rows = connection.execute(
                "SELECT id, api FROM providers",
            ).fetchall()
            model_rows = connection.execute(
                """
                SELECT provider_id, id, provider_api
                FROM models
                WHERE provider_api IS NOT NULL
                """,
            ).fetchall()
        finally:
            connection.close()
    except sqlite3.Error as error:
        raise BaselineUnavailableError(
            f"unable to inspect baseline database {path}: {error}",
        ) from error

    provider_urls = {
        str(provider_id): _optional_string(
            api_url,
            f"provider {provider_id}.api",
        )
        for provider_id, api_url in provider_rows
    }
    model_urls = {
        (str(provider_id), str(model_id)): _string(
            api_url,
            f"model {provider_id}/{model_id}.provider.api",
        )
        for provider_id, model_id, api_url in model_rows
    }
    return Baseline(
        version=version,
        state=ApiUrlState(
            provider_ids=frozenset(provider_urls),
            provider_urls=provider_urls,
            model_urls=model_urls,
        ),
    )


def compare_api_urls(
    baseline: ApiUrlState,
    current: ApiUrlState,
) -> list[ApiUrlChange]:
    """Return URL changes for existing providers and their model overrides."""
    changes: list[ApiUrlChange] = []
    # New providers are allowed to introduce URLs. The protected case is URL
    # routing changing for provider IDs that consumers may already trust.
    for provider_id in sorted(baseline.provider_ids):
        previous_url = baseline.provider_urls.get(provider_id)
        current_url = current.provider_urls.get(provider_id)
        if previous_url != current_url:
            changes.append(
                ApiUrlChange(
                    kind="Provider",
                    provider_id=provider_id,
                    model_id=None,
                    previous_url=previous_url,
                    current_url=current_url,
                ),
            )

    # Model provider overrides can route individual model calls differently from
    # the provider default, so protect those URLs under known providers too.
    baseline_model_keys = {
        key for key in baseline.model_urls if key[0] in baseline.provider_ids
    }
    current_model_keys = {
        key for key in current.model_urls if key[0] in baseline.provider_ids
    }
    for provider_id, model_id in sorted(
        baseline_model_keys | current_model_keys,
    ):
        previous_url = baseline.model_urls.get((provider_id, model_id))
        current_url = current.model_urls.get((provider_id, model_id))
        if previous_url != current_url:
            changes.append(
                ApiUrlChange(
                    kind="Model override",
                    provider_id=provider_id,
                    model_id=model_id,
                    previous_url=previous_url,
                    current_url=current_url,
                ),
            )

    return changes


def render_markdown(
    *,
    changes: list[ApiUrlChange],
    baseline_version: str,
    digest: str,
    fail_closed_reason: str = "",
) -> str:
    """Render the workflow summary shown before environment approval."""
    lines = ["## Upstream API URL Check", ""]
    lines.append(f"Baseline: `{_display_baseline(baseline_version)}`")
    if digest:
        lines.append(f"Current upstream JSON SHA256: `{digest}`")
    lines.append("")

    if fail_closed_reason:
        lines.extend(
            [
                "Manual approval is required because the previous published "
                "package could not be inspected.",
                "",
                f"Reason: `{html.escape(fail_closed_reason)}`",
                "",
            ],
        )
        return "\n".join(lines)

    if not changes:
        lines.append(
            "No protected upstream API URL changes were detected for "
            "previously-known providers.",
        )
        lines.append("")
        return "\n".join(lines)

    # Environment-approval jobs pause before steps run, so render the diff in
    # the planning job summary and point the approval environment URL at it.
    lines.append(
        f"Manual approval is required for {len(changes)} protected upstream "
        "API URL change(s).",
    )
    lines.append("")
    lines.extend(
        [
            "| Type | Provider | Model | Previous URL | Current URL |",
            "| --- | --- | --- | --- | --- |",
        ],
    )
    for change in changes:
        lines.append(
            "| "
            f"{_table_text(change.kind)} | "
            f"{_code(change.provider_id)} | "
            f"{_model_cell(change.model_id)} | "
            f"{_url_cell(change.previous_url)} | "
            f"{_url_cell(change.current_url)} |",
        )
    lines.append("")
    return "\n".join(lines)


def _empty_state() -> ApiUrlState:
    return ApiUrlState(
        provider_ids=frozenset(),
        provider_urls={},
        model_urls={},
    )


def _request(url: str) -> urllib.request.Request:
    return urllib.request.Request(
        url,
        headers={"User-Agent": "modelsdotdev-python-publish"},
    )


def _pypi_version(data: Any) -> str:
    if not isinstance(data, dict):
        raise BaselineUnavailableError("PyPI metadata root is not an object")
    info = _object(data.get("info"), "PyPI metadata info")
    version = _string(info.get("version"), "PyPI latest version")
    if not version:
        raise BaselineUnavailableError("PyPI latest version is empty")
    return version


def _wheel_url(data: Any, version: str) -> str:
    if not isinstance(data, dict):
        raise BaselineUnavailableError("PyPI metadata root is not an object")
    releases = _object(data.get("releases"), "PyPI metadata releases")
    files = releases.get(version)
    if not isinstance(files, list):
        raise BaselineUnavailableError(
            f"PyPI release {version} has no files",
        )

    for release_file in files:
        file_info = _object(release_file, f"PyPI release {version} file")
        if file_info.get("packagetype") != "bdist_wheel":
            continue
        return _string(
            file_info.get("url"),
            f"PyPI release {version} wheel URL",
        )
    raise BaselineUnavailableError(f"PyPI release {version} has no wheel")


def _object(value: Any, context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TypeError(f"{context} must be an object")
    return value


def _optional_string(value: Any, context: str) -> str | None:
    if value is None:
        return None
    return _string(value, context)


def _string(value: Any, context: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{context} must be a string")
    return value


def _summary(changes: list[ApiUrlChange], fail_closed_reason: str) -> str:
    if fail_closed_reason:
        return (
            "Manual approval required: baseline package could not be inspected"
        )
    if changes:
        return (
            f"Manual approval required: {len(changes)} protected upstream "
            "API URL change(s) detected"
        )
    return "No protected upstream API URL changes detected"


def _change_payload(change: ApiUrlChange) -> dict[str, str | None]:
    return {
        "type": change.kind,
        "provider": change.provider_id,
        "model": change.model_id,
        "previous_url": change.previous_url,
        "current_url": change.current_url,
    }


def _write_github_outputs(outputs: dict[str, str]) -> None:
    output_path = os.environ.get("GITHUB_OUTPUT")
    if not output_path:
        return

    with Path(output_path).open("a", encoding="utf-8") as output_file:
        for name, value in outputs.items():
            if "\n" not in value:
                output_file.write(f"{name}={value}\n")
                continue
            delimiter = f"EOF_{hashlib.sha256(uuid.uuid4().bytes).hexdigest()}"
            output_file.write(f"{name}<<{delimiter}\n{value}\n{delimiter}\n")


def _display_baseline(version: str) -> str:
    if version == "none":
        return "no published baseline"
    return f"modelsdotdev {version}"


def _table_text(value: str) -> str:
    return html.escape(value).replace("|", "&#124;")


def _code(value: str) -> str:
    return f"<code>{_table_text(value)}</code>"


def _model_cell(model_id: str | None) -> str:
    if model_id is None:
        return "<em>provider default</em>"
    return _code(model_id)


def _url_cell(url: str | None) -> str:
    if url is None:
        return "<em>not set</em>"
    return _code(url)


if __name__ == "__main__":
    raise SystemExit(main())
