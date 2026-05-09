"""Build the bundled SQLite database from models.dev JSON."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from urllib.request import Request, urlopen

from modelsdotdev._internal.schema import (
    CREATE_SCHEMA_SQL,
    EXPERIMENTAL_MODE_INSERT_SQL,
    MODEL_INSERT_SQL,
    MODEL_MODALITY_INSERT_SQL,
    PRICING_INSERT_SQL,
    PROVIDER_INSERT_SQL,
)

API_URL = "https://models.dev/api.json"
DEFAULT_OUTPUT = Path(__file__).parents[1] / "_db.sqlite"

type JsonValue = str | int | float | bool | JsonObject | JsonArray | None
type JsonObject = dict[str, JsonValue]
type JsonArray = list[JsonValue]
type PricingValues = tuple[
    int,
    float,
    float,
    float | None,
    float | None,
    float | None,
    float | None,
    float | None,
]
type ProviderConfigValues = tuple[str | None, ...]


def main() -> int:
    """Download models.dev data and build the bundled SQLite DB."""
    parser = argparse.ArgumentParser(
        description="Download models.dev data and build the bundled SQLite DB.",
    )
    parser.add_argument(
        "--source",
        default=API_URL,
        help="JSON source URL or local file path.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="SQLite database output path.",
    )
    args = parser.parse_args()

    source = cast("str", args.source)
    output = cast("Path", args.output)
    provider_count, model_count = generate_database(
        source=source,
        output=output,
    )
    message = (
        f"Wrote {output} with {provider_count} providers "
        f"and {model_count} models\n"
    )
    sys.stdout.write(
        message,
    )
    return 0


def generate_database(
    source: str = API_URL,
    output: Path = DEFAULT_OUTPUT,
) -> tuple[int, int]:
    """Generate the SQLite database and return provider/model counts."""
    providers = _load_providers(source)
    _write_database(providers, output, source)
    provider_count = len(providers)
    model_count = sum(
        len(_object(provider, "models", f"provider {provider_id}"))
        for provider_id, provider in providers.items()
    )
    return provider_count, model_count


def _load_providers(source: str) -> dict[str, JsonObject]:
    if source.startswith(("http://", "https://")):
        request = Request(
            source,
            headers={"User-Agent": "modelsdotdev-python-sync"},
        )
        with urlopen(request, timeout=60) as response:
            payload = response.read()
    else:
        payload = Path(source).expanduser().read_bytes()

    data = cast("JsonValue", json.loads(payload))
    if not isinstance(data, dict):
        raise TypeError("models.dev JSON root must be an object")
    return {
        key: _as_object(value, f"provider {key}") for key, value in data.items()
    }


def _write_database(
    providers: dict[str, JsonObject],
    output: Path,
    source: str,
) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f"{output.name}.tmp")
    if temporary.exists():
        temporary.unlink()

    with closing(sqlite3.connect(temporary)) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript(CREATE_SCHEMA_SQL)

        model_count = 0
        for provider_id, provider in sorted(providers.items()):
            models = _object(provider, "models", f"provider {provider_id}")
            stored_provider_id = _string(
                provider,
                "id",
                f"provider {provider_id}",
            )
            if stored_provider_id != provider_id:
                raise ValueError(
                    f"provider key {provider_id!r} does not match id "
                    f"{stored_provider_id!r}",
                )

            connection.execute(
                PROVIDER_INSERT_SQL,
                (
                    provider_id,
                    _string(provider, "name", f"provider {provider_id}"),
                    _string(provider, "npm", f"provider {provider_id}"),
                    _optional_string(
                        provider,
                        "api",
                        f"provider {provider_id}",
                    ),
                    _string(provider, "doc", f"provider {provider_id}"),
                    _join_env(
                        _array(provider, "env", f"provider {provider_id}")
                    ),
                ),
            )

            for model_id, raw_model in sorted(models.items()):
                model = _as_object(raw_model, f"model {provider_id}/{model_id}")
                full_id = f"{provider_id}:{model_id}"
                stored_model_id = _string(
                    model,
                    "id",
                    f"model {provider_id}/{model_id}",
                )
                if stored_model_id != model_id:
                    raise ValueError(
                        f"model key {provider_id}/{model_id!r} does not match "
                        f"id {stored_model_id!r}",
                    )

                connection.execute(
                    MODEL_INSERT_SQL,
                    (
                        full_id,
                        provider_id,
                        model_id,
                        _string(
                            model,
                            "name",
                            f"model {provider_id}/{model_id}",
                        ),
                        _optional_string(
                            model,
                            "family",
                            f"model {provider_id}/{model_id}",
                        ),
                        _bool(model, "attachment", full_id),
                        _bool(model, "reasoning", full_id),
                        _bool(model, "tool_call", full_id),
                        _interleaved_field(model.get("interleaved")),
                        _optional_bool(model, "structured_output", full_id),
                        _optional_bool(model, "temperature", full_id),
                        _optional_string(model, "knowledge", full_id),
                        _bool(model, "open_weights", full_id),
                        *_limit_values(_object(model, "limit", full_id)),
                        _optional_string(
                            model,
                            "status",
                            f"model {provider_id}/{model_id}",
                        ),
                        *_provider_config_values(
                            _maybe_object(model, "provider", full_id),
                        ),
                    ),
                )
                modalities = _object(model, "modalities", full_id)
                _insert_modalities(connection, full_id, modalities)
                _insert_pricing(
                    connection,
                    full_id,
                    None,
                    _maybe_object(model, "cost", full_id),
                )
                _insert_experimental_modes(
                    connection,
                    full_id,
                    _maybe_object(model, "experimental", full_id),
                )
                model_count += 1

        generated_at = datetime.now(tz=UTC).isoformat(timespec="seconds")
        connection.executemany(
            "INSERT INTO metadata (key, value) VALUES (?, ?)",
            [
                ("source", source),
                ("generated_at", generated_at),
                ("provider_count", str(len(providers))),
                ("model_count", str(model_count)),
            ],
        )
        connection.execute("PRAGMA optimize")
        connection.commit()

    os.replace(temporary, output)


def _as_object(value: JsonValue, context: str) -> JsonObject:
    if not isinstance(value, dict):
        raise TypeError(f"{context} must be an object")
    return value


def _object(data: JsonObject, key: str, context: str) -> JsonObject:
    value = data.get(key)
    if not isinstance(value, dict):
        raise TypeError(f"{context}.{key} must be an object")
    return value


def _array(data: JsonObject, key: str, context: str) -> JsonArray:
    value = data.get(key)
    if not isinstance(value, list):
        raise TypeError(f"{context}.{key} must be an array")
    return value


def _maybe_object(
    data: JsonObject,
    key: str,
    context: str,
) -> JsonObject | None:
    value = data.get(key)
    if value is None:
        return None
    return _as_object(value, f"{context}.{key}")


def _as_string(value: JsonValue, context: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{context} must be a string")
    return value


def _join_env(values: JsonArray) -> str:
    env = tuple(_as_string(value, "provider env") for value in values)
    if any(";" in value for value in env):
        raise ValueError("provider env values cannot contain ';'")
    return ";".join(env)


def _string(data: JsonObject, key: str, context: str) -> str:
    value = data.get(key)
    if not isinstance(value, str):
        raise TypeError(f"{context}.{key} must be a string")
    return value


def _optional_string(data: JsonObject, key: str, context: str) -> str | None:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"{context}.{key} must be a string")
    return value


def _bool(data: JsonObject, key: str, context: str) -> bool:
    value = data.get(key)
    if not isinstance(value, bool):
        raise TypeError(f"{context}.{key} must be a boolean")
    return value


def _optional_bool(data: JsonObject, key: str, context: str) -> bool | None:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, bool):
        raise TypeError(f"{context}.{key} must be a boolean")
    return value


def _interleaved_field(value: JsonValue) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    interleaved = _as_object(value, "interleaved")
    return _string(interleaved, "field", "interleaved")


def _pricing_values(cost: JsonObject) -> list[PricingValues]:
    base = _pricing_value(cost, 0, "cost")
    tiers = [base]
    for key, value in sorted(cost.items()):
        min_context = _context_threshold(key)
        if min_context is None:
            continue
        tier = _pricing_value(
            _as_object(value, f"cost.{key}"),
            min_context,
            f"cost.{key}",
        )
        if any(_same_pricing(existing, tier) for existing in tiers):
            continue
        tiers.append(tier)
    return tiers


def _pricing_value(
    cost: JsonObject,
    min_context: int,
    context: str,
) -> PricingValues:
    return (
        min_context,
        _number(cost, "input", context),
        _number(cost, "output", context),
        _optional_number(cost, "reasoning"),
        _optional_number(cost, "cache_read"),
        _optional_number(cost, "cache_write"),
        _optional_number(cost, "input_audio"),
        _optional_number(cost, "output_audio"),
    )


def _context_threshold(key: str) -> int | None:
    prefix = "context_over_"
    if not key.startswith(prefix):
        return None
    suffix = key.removeprefix(prefix)
    multipliers = {
        "k": 1_000,
        "m": 1_000_000,
        "b": 1_000_000_000,
    }
    multiplier = multipliers.get(suffix[-1:].lower(), 1)
    digits = suffix[:-1] if multiplier != 1 else suffix
    if not digits.isdecimal():
        raise ValueError(f"cost.{key} context threshold is not numeric")
    return int(digits) * multiplier


def _same_pricing(left: PricingValues, right: PricingValues) -> bool:
    return left[1:] == right[1:]


def _number(data: JsonObject, key: str, context: str) -> float:
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError(f"{context}.{key} must be a number")
    return float(value)


def _optional_number(data: JsonObject, key: str) -> float | None:
    value = data.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError(f"{key} must be a number")
    return float(value)


def _limit_values(limit: JsonObject) -> tuple[int, int | None, int]:
    return (
        int(_number(limit, "context", "limit")),
        None
        if limit.get("input") is None
        else int(_number(limit, "input", "limit")),
        int(_number(limit, "output", "limit")),
    )


def _provider_config_values(config: JsonObject | None) -> ProviderConfigValues:
    if config is None:
        return (None,) * 5
    return (
        _optional_string(config, "npm", "provider config"),
        _optional_string(config, "api", "provider config"),
        _optional_string(config, "shape", "provider config"),
        _optional_json(config, "body"),
        _optional_json(config, "headers"),
    )


def _optional_json(data: JsonObject, key: str) -> str | None:
    value = data.get(key)
    return None if value is None else _json(value)


def _insert_modalities(
    connection: sqlite3.Connection,
    full_id: str,
    modalities: JsonObject,
) -> None:
    rows: list[tuple[str, str, int, str]] = []
    for direction in ("input", "output"):
        values = _array(modalities, direction, f"{full_id}.modalities")
        rows.extend(
            (full_id, direction, position, _as_string(value, "modality"))
            for position, value in enumerate(values)
        )
    connection.executemany(
        MODEL_MODALITY_INSERT_SQL,
        rows,
    )


def _insert_pricing(
    connection: sqlite3.Connection,
    full_id: str,
    experimental_mode_name: str | None,
    cost: JsonObject | None,
) -> None:
    if cost is None:
        return
    rows = [
        (full_id, experimental_mode_name, *values)
        for values in _pricing_values(cost)
    ]
    connection.executemany(
        PRICING_INSERT_SQL,
        rows,
    )


def _insert_experimental_modes(
    connection: sqlite3.Connection,
    full_id: str,
    experimental: JsonObject | None,
) -> None:
    if experimental is None:
        return
    modes = _maybe_object(experimental, "modes", f"{full_id}.experimental")
    if modes is None:
        return
    rows = []
    pricing: list[tuple[str, JsonObject | None]] = []
    for name, raw_mode in sorted(modes.items()):
        mode = _as_object(raw_mode, f"{full_id}.experimental.modes.{name}")
        rows.append(
            (
                full_id,
                name,
                *_provider_config_values(
                    _maybe_object(mode, "provider", f"{full_id}.{name}"),
                ),
            ),
        )
        pricing.append((name, _maybe_object(mode, "cost", f"{full_id}.{name}")))
    connection.executemany(
        EXPERIMENTAL_MODE_INSERT_SQL,
        rows,
    )
    for name, cost in pricing:
        _insert_pricing(connection, full_id, name, cost)


def _json(value: JsonValue) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


if __name__ == "__main__":
    raise SystemExit(main())
