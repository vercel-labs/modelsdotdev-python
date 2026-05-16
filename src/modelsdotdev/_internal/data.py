from __future__ import annotations

import json
import os
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, cast

from modelsdotdev._internal.schema import MODEL_COLUMNS, PROVIDER_COLUMNS

if TYPE_CHECKING:
    from collections.abc import Iterator

type JsonValue = str | int | float | bool | JsonObject | JsonArray | None
"""JSON scalar, object, or array value."""

type JsonObject = dict[str, JsonValue]
"""JSON object payload."""

type JsonArray = list[JsonValue]
"""JSON array payload."""


class Capability(StrEnum):
    """Model capability flag."""

    ATTACHMENT = "attachment"
    REASONING = "reasoning"
    STRUCTURED_OUTPUT = "structured_output"
    TEMPERATURE = "temperature"
    TOOL_CALL = "tool_call"


class Modality(StrEnum):
    """Supported input/output modality."""

    TEXT = "text"
    AUDIO = "audio"
    IMAGE = "image"
    VIDEO = "video"
    PDF = "pdf"


class ProviderAPIShape(StrEnum):
    """Provider API shape override."""

    RESPONSES = "responses"
    COMPLETIONS = "completions"


class Status(StrEnum):
    """Model lifecycle status."""

    ALPHA = "alpha"
    BETA = "beta"
    DEPRECATED = "deprecated"


@dataclass(frozen=True, kw_only=True, slots=True)
class Cost:
    """Token and media pricing tier."""

    input: float
    """Input token cost."""

    output: float
    """Output token cost."""

    min_context: int = 0
    """Minimum context size where this pricing applies."""

    reasoning: float | None = None
    """Reasoning token cost."""

    cache_read: float | None = None
    """Cache-read token cost."""

    cache_write: float | None = None
    """Cache-write token cost."""

    input_audio: float | None = None
    """Audio input cost."""

    output_audio: float | None = None
    """Audio output cost."""


@dataclass(frozen=True, kw_only=True, slots=True)
class Limits:
    """Token limits."""

    context: int
    """Context window."""

    output: int
    """Maximum output tokens."""

    input: int | None = None
    """Maximum input tokens."""


@dataclass(frozen=True, kw_only=True, slots=True)
class Modalities:
    """Input and output media support."""

    input: tuple[Modality, ...]
    """Accepted input modalities."""

    output: tuple[Modality, ...]
    """Produced output modalities."""


@dataclass(frozen=True, kw_only=True, slots=True)
class ModelProviderConfig:
    """Model-specific provider override."""

    npm: str | None = None
    """AI SDK provider package."""

    api: str | None = None
    """Provider API base URL."""

    api_shape: ProviderAPIShape | None = None
    """Request/response shape."""

    body: JsonObject | None = None
    """Extra request body."""

    headers: dict[str, str] | None = None
    """Extra request headers."""


@dataclass(frozen=True, kw_only=True, slots=True)
class ModelRef:
    """Parsed model identifier."""

    provider_id: str | None
    """Provider ID, if the model ID is provider-qualified."""

    model_id: str
    """Provider-specific model ID."""


@dataclass(frozen=True, kw_only=True, slots=True)
class ExperimentalMode:
    """Experimental model mode."""

    cost: list[Cost] | None = None
    """Mode-specific pricing tiers."""

    provider: ModelProviderConfig | None = None
    """Mode provider override."""


@dataclass(frozen=True, kw_only=True, slots=True)
class Provider:
    """Model provider."""

    id: str
    """Stable provider ID."""

    name: str
    """Display name."""

    env: tuple[str, ...]
    """Environment variable names."""

    npm: str
    """AI SDK provider package."""

    doc: str
    """Model documentation URL."""

    api: str | None = None
    """Provider API base URL."""

    def get_model_by_id(self, model_id: str) -> Model | None:
        return _get_model_by_provider_id(self.id, model_id)

    def iter_models(self) -> Iterator[Model]:
        return _iter_models_for_provider_id(self.id)


@dataclass(frozen=True, kw_only=True, slots=True)
class Model:
    """Provider-hosted model."""

    provider_id: str
    """Owning provider ID."""

    id: str
    """Provider-local model ID."""

    name: str
    """Display name."""

    capabilities: frozenset[Capability]
    """Supported model capabilities."""

    modalities: Modalities
    """Input/output modalities."""

    open_weights: bool
    """Whether weights are open."""

    limits: Limits
    """Token limits."""

    family: str | None = None
    """Model family."""

    interleaved_field: str | None = None
    """Interleaving field, if specified."""

    knowledge_cutoff: str | None = None
    """Knowledge cutoff."""

    cost: list[Cost] | None = None
    """Pricing tiers."""

    status: Status | None = None
    """Lifecycle status."""

    experimental_modes: dict[str, ExperimentalMode] | None = None
    """Experimental modes keyed by name."""

    provider_config: ModelProviderConfig | None = None
    """Provider override."""

    @property
    def qualified_id(self) -> str:
        return f"{self.provider_id}:{self.id}"


DATABASE_PATH_ENV = "MODELDOTDEV_DATABASE_PATH"
DB_PATH = Path(__file__).parents[1] / "_db.sqlite"


def get_provider_by_name(name: str) -> Provider | None:
    """Return a provider by display name, using case-insensitive matching."""
    with closing(_connect()) as connection:
        row = connection.execute(
            f"SELECT {PROVIDER_COLUMNS} FROM providers "
            "WHERE name = ? COLLATE NOCASE",
            (name,),
        ).fetchone()
        return None if row is None else _provider_from_row(row)


def get_provider_by_id(provider_id: str) -> Provider | None:
    with closing(_connect()) as connection:
        row = connection.execute(
            f"SELECT {PROVIDER_COLUMNS} FROM providers WHERE id = ?",
            (provider_id,),
        ).fetchone()
        return None if row is None else _provider_from_row(row)


def parse_model_id(model_id: str) -> ModelRef:
    """Parse a possibly provider-qualified model ID."""
    if not model_id:
        raise ValueError("model_id must not be empty")
    prefix, sep, suffix = model_id.partition(":")
    if sep:
        if not prefix or not suffix:
            raise ValueError(f"malformed model_id: {model_id}")
        return ModelRef(provider_id=prefix, model_id=suffix)
    return ModelRef(provider_id=None, model_id=prefix)


def get_model_by_id(model_id: str) -> Model | None:
    """Return a model by ``provider:model`` or ``provider/model`` ID."""
    invalid_message = (
        "model_id must include provider and model IDs "
        "as 'provider:model' or 'provider/model'"
    )
    colon_index = model_id.find(":")
    slash_index = model_id.find("/")
    if colon_index == -1 and slash_index == -1:
        raise ValueError(invalid_message)

    separator = (
        "/"
        if slash_index != -1
        and (colon_index == -1 or slash_index < colon_index)
        else ":"
    )
    provider_id, provider_model_id = model_id.split(separator, 1)

    if not provider_id or not provider_model_id:
        raise ValueError(invalid_message)

    with closing(_connect()) as connection:
        row = connection.execute(
            f"SELECT {MODEL_COLUMNS} FROM models WHERE full_id = ?",
            (f"{provider_id}:{provider_model_id}",),
        ).fetchone()
        return None if row is None else _model_from_row(connection, row)


def iter_providers() -> Iterator[Provider]:
    with closing(_connect()) as connection:
        providers = tuple(
            _provider_from_row(row)
            for row in connection.execute(
                f"SELECT {PROVIDER_COLUMNS} FROM providers "
                "ORDER BY name COLLATE NOCASE",
            )
        )
    return iter(providers)


def iter_models() -> Iterator[Model]:
    with closing(_connect()) as connection:
        models = tuple(
            _model_from_row(connection, row)
            for row in connection.execute(
                f"SELECT {MODEL_COLUMNS} FROM models ORDER BY full_id",
            )
        )
    return iter(models)


def _iter_models_for_provider_id(provider_id: str) -> Iterator[Model]:
    with closing(_connect()) as connection:
        models = tuple(
            _model_from_row(connection, row)
            for row in connection.execute(
                f"""
                SELECT {MODEL_COLUMNS}
                FROM models
                WHERE provider_id = ?
                ORDER BY id
                """,
                (provider_id,),
            )
        )
    return iter(models)


def _get_model_by_provider_id(provider_id: str, model_id: str) -> Model | None:
    with closing(_connect()) as connection:
        row = connection.execute(
            f"""
            SELECT {MODEL_COLUMNS}
            FROM models
            WHERE provider_id = ? AND id = ?
            """,
            (provider_id, model_id),
        ).fetchone()
        return None if row is None else _model_from_row(connection, row)


def _connect() -> sqlite3.Connection:
    db_path = _database_path()
    if not db_path.is_file():
        raise FileNotFoundError(_missing_database_message(db_path))
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _database_path() -> Path:
    if db_path := os.environ.get(DATABASE_PATH_ENV):
        return Path(db_path).expanduser()
    return DB_PATH


def _missing_database_message(db_path: Path) -> str:
    message = f"modelsdotdev database not found at {db_path}"
    if os.environ.get(DATABASE_PATH_ENV):
        return f"{message}; check {DATABASE_PATH_ENV} or unset it"
    if _source_checkout_root() is not None:
        return f"{message}; run `uv run poe generate-db` to create it"
    return f"{message}; reinstall the package or report missing package data"


def _source_checkout_root() -> Path | None:
    root = Path(__file__).parents[3]
    if (root / "pyproject.toml").is_file() and (
        root / "src" / "modelsdotdev"
    ).is_dir():
        return root
    return None


def _provider_from_row(row: sqlite3.Row) -> Provider:
    provider_id = cast("str", row["id"])
    return Provider(
        id=provider_id,
        name=cast("str", row["name"]),
        env=tuple(cast("str", row["env"]).split(";")),
        npm=cast("str", row["npm"]),
        api=cast("str | None", row["api"]),
        doc=cast("str", row["doc"]),
    )


def _model_from_row(
    connection: sqlite3.Connection,
    row: sqlite3.Row,
) -> Model:
    full_id = cast("str", row["full_id"])
    return Model(
        provider_id=cast("str", row["provider_id"]),
        id=cast("str", row["id"]),
        name=cast("str", row["name"]),
        family=cast("str | None", row["family"]),
        capabilities=_capabilities(row),
        interleaved_field=_interleaved_field(row),
        knowledge_cutoff=cast("str | None", row["knowledge"]),
        modalities=_modalities(connection, full_id),
        open_weights=bool(row["open_weights"]),
        cost=_cost(connection, full_id, None),
        limits=Limits(
            context=cast("int", row["limit_context"]),
            input=cast("int | None", row["limit_input"]),
            output=cast("int", row["limit_output"]),
        ),
        status=_status(row),
        experimental_modes=_experimental_modes(connection, full_id),
        provider_config=_provider_config(row, "provider"),
    )


def _capabilities(row: sqlite3.Row) -> frozenset[Capability]:
    capabilities: set[Capability] = set()
    for capability in Capability:
        if row[capability.value]:
            capabilities.add(capability)
    return frozenset(capabilities)


def _status(row: sqlite3.Row) -> Status | None:
    status = row["status"]
    return None if status is None else Status(cast("str", status))


def _modalities(connection: sqlite3.Connection, full_id: str) -> Modalities:
    rows = connection.execute(
        """
        SELECT direction, value FROM model_modalities
        WHERE model_full_id = ?
        ORDER BY direction, position
        """,
        (full_id,),
    )
    values: dict[str, list[Modality]] = {"input": [], "output": []}
    for row in rows:
        direction = cast("str", row["direction"])
        values[direction].append(Modality(cast("str", row["value"])))
    return Modalities(
        input=tuple(values["input"]),
        output=tuple(values["output"]),
    )


def _interleaved_field(row: sqlite3.Row) -> str | None:
    return cast("str | None", row["interleaved_field"])


def _cost(
    connection: sqlite3.Connection,
    full_id: str,
    experimental_mode_name: str | None,
) -> list[Cost] | None:
    rows = list(
        connection.execute(
            """
            SELECT * FROM pricing
            WHERE model_full_id = ?
              AND experimental_mode_name IS ?
            ORDER BY min_context
            """,
            (full_id, experimental_mode_name),
        ),
    )
    if not rows:
        return None
    return [
        Cost(
            input=cast("float", row["cost_input"]),
            output=cast("float", row["cost_output"]),
            min_context=cast("int", row["min_context"]),
            reasoning=cast("float | None", row["cost_reasoning"]),
            cache_read=cast("float | None", row["cost_cache_read"]),
            cache_write=cast("float | None", row["cost_cache_write"]),
            input_audio=cast("float | None", row["cost_input_audio"]),
            output_audio=cast("float | None", row["cost_output_audio"]),
        )
        for row in rows
    ]


def _provider_config(
    row: sqlite3.Row,
    prefix: str,
) -> ModelProviderConfig | None:
    npm = row[f"{prefix}_npm"]
    api = row[f"{prefix}_api"]
    api_shape = row[f"{prefix}_api_shape"]
    body = row[f"{prefix}_body_json"]
    headers = row[f"{prefix}_headers_json"]
    if (
        npm is None
        and api is None
        and api_shape is None
        and body is None
        and headers is None
    ):
        return None
    return ModelProviderConfig(
        npm=cast("str | None", npm),
        api=cast("str | None", api),
        api_shape=(
            None
            if api_shape is None
            else ProviderAPIShape(cast("str", api_shape))
        ),
        body=_json_object(body),
        headers=_json_string_object(headers),
    )


def _json_object(value: object) -> JsonObject | None:
    if value is None:
        return None
    return cast("JsonObject", json.loads(cast("str", value)))


def _json_string_object(value: object) -> dict[str, str] | None:
    if value is None:
        return None
    return cast("dict[str, str]", json.loads(cast("str", value)))


def _experimental_modes(
    connection: sqlite3.Connection,
    full_id: str,
) -> dict[str, ExperimentalMode] | None:
    rows = list(
        connection.execute(
            """
            SELECT * FROM experimental_modes
            WHERE model_full_id = ?
            ORDER BY name
            """,
            (full_id,),
        ),
    )
    if not rows:
        return None
    return {
        cast("str", row["name"]): ExperimentalMode(
            cost=_cost(connection, full_id, cast("str", row["name"])),
            provider=_provider_config(row, "provider"),
        )
        for row in rows
    }
