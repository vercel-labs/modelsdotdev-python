"""Shared SQLite schema definitions."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, kw_only=True, slots=True)
class Column:
    """SQLite table column definition."""

    name: str
    definition: str


@dataclass(frozen=True, kw_only=True, slots=True)
class Table:
    """SQLite table definition."""

    name: str
    columns: tuple[Column, ...]
    constraints: tuple[str, ...] = ()
    indexes: tuple[str, ...] = ()


def _column_list(table: Table) -> str:
    return ", ".join(column.name for column in table.columns)


def _insert_sql(table: Table) -> str:
    columns = _column_list(table)
    placeholders = ", ".join("?" for _ in table.columns)
    return f"INSERT INTO {table.name} ({columns}) VALUES ({placeholders})"


def _create_table_sql(table: Table) -> str:
    definitions = [
        f"{column.name} {column.definition}" for column in table.columns
    ]
    definitions.extend(table.constraints)
    joined_definitions = ",\n    ".join(definitions)
    return f"CREATE TABLE {table.name} (\n    {joined_definitions}\n);"


def _schema_sql(tables: tuple[Table, ...]) -> str:
    statements = []
    for table in tables:
        statements.append(_create_table_sql(table))
        statements.extend(table.indexes)
    return "\n\n".join(statements)


PRICING_COLUMNS = (
    Column(name="cost_input", definition="REAL"),
    Column(name="cost_output", definition="REAL"),
    Column(name="cost_reasoning", definition="REAL"),
    Column(name="cost_cache_read", definition="REAL"),
    Column(name="cost_cache_write", definition="REAL"),
    Column(name="cost_input_audio", definition="REAL"),
    Column(name="cost_output_audio", definition="REAL"),
)

PROVIDER_CONFIG_COLUMNS = (
    Column(name="provider_npm", definition="TEXT"),
    Column(name="provider_api", definition="TEXT"),
    Column(name="provider_api_shape", definition="TEXT"),
    Column(name="provider_body_json", definition="TEXT"),
    Column(name="provider_headers_json", definition="TEXT"),
)

METADATA = Table(
    name="metadata",
    columns=(
        Column(name="key", definition="TEXT PRIMARY KEY"),
        Column(name="value", definition="TEXT NOT NULL"),
    ),
)

PROVIDERS = Table(
    name="providers",
    columns=(
        Column(name="id", definition="TEXT PRIMARY KEY"),
        Column(name="name", definition="TEXT NOT NULL"),
        Column(name="npm", definition="TEXT NOT NULL"),
        Column(name="api", definition="TEXT"),
        Column(name="doc", definition="TEXT NOT NULL"),
        Column(name="env", definition="TEXT NOT NULL"),
    ),
    indexes=(
        "CREATE UNIQUE INDEX providers_name_nocase_idx "
        "ON providers(name COLLATE NOCASE);",
    ),
)

MODELS = Table(
    name="models",
    columns=(
        Column(name="full_id", definition="TEXT PRIMARY KEY"),
        Column(
            name="provider_id",
            definition=(
                "TEXT NOT NULL REFERENCES providers(id) ON DELETE CASCADE"
            ),
        ),
        Column(name="id", definition="TEXT NOT NULL"),
        Column(name="name", definition="TEXT NOT NULL"),
        Column(name="family", definition="TEXT"),
        Column(name="attachment", definition="INTEGER NOT NULL"),
        Column(name="reasoning", definition="INTEGER NOT NULL"),
        Column(name="tool_call", definition="INTEGER NOT NULL"),
        Column(name="interleaved_field", definition="TEXT"),
        Column(name="structured_output", definition="INTEGER"),
        Column(name="temperature", definition="INTEGER"),
        Column(name="knowledge", definition="TEXT"),
        Column(name="open_weights", definition="INTEGER NOT NULL"),
        Column(name="limit_context", definition="INTEGER NOT NULL"),
        Column(name="limit_input", definition="INTEGER"),
        Column(name="limit_output", definition="INTEGER NOT NULL"),
        Column(name="status", definition="TEXT"),
        *PROVIDER_CONFIG_COLUMNS,
    ),
    indexes=(
        "CREATE INDEX models_provider_id_idx ON models(provider_id);",
        "CREATE INDEX models_id_idx ON models(id);",
    ),
)

MODEL_MODALITIES = Table(
    name="model_modalities",
    columns=(
        Column(
            name="model_full_id",
            definition=(
                "TEXT NOT NULL REFERENCES models(full_id) ON DELETE CASCADE"
            ),
        ),
        Column(name="direction", definition="TEXT NOT NULL"),
        Column(name="position", definition="INTEGER NOT NULL"),
        Column(name="value", definition="TEXT NOT NULL"),
    ),
    constraints=("PRIMARY KEY (model_full_id, direction, position)",),
)

EXPERIMENTAL_MODES = Table(
    name="experimental_modes",
    columns=(
        Column(
            name="model_full_id",
            definition=(
                "TEXT NOT NULL REFERENCES models(full_id) ON DELETE CASCADE"
            ),
        ),
        Column(name="name", definition="TEXT NOT NULL"),
        *PROVIDER_CONFIG_COLUMNS,
    ),
    constraints=("PRIMARY KEY (model_full_id, name)",),
)

PRICING = Table(
    name="pricing",
    columns=(
        Column(
            name="model_full_id",
            definition=(
                "TEXT NOT NULL REFERENCES models(full_id) ON DELETE CASCADE"
            ),
        ),
        Column(name="experimental_mode_name", definition="TEXT"),
        Column(name="min_context", definition="INTEGER NOT NULL"),
        *PRICING_COLUMNS,
    ),
    constraints=(
        "CHECK (min_context >= 0)",
        "CHECK (cost_input IS NOT NULL)",
        "CHECK (cost_output IS NOT NULL)",
        "FOREIGN KEY (model_full_id, experimental_mode_name) "
        "REFERENCES experimental_modes(model_full_id, name) "
        "ON DELETE CASCADE",
    ),
    indexes=(
        "CREATE UNIQUE INDEX pricing_owner_min_context_idx "
        "ON pricing(model_full_id, "
        "COALESCE(experimental_mode_name, ''), min_context);",
        "CREATE INDEX pricing_experimental_mode_idx "
        "ON pricing(model_full_id, experimental_mode_name);",
    ),
)

TABLES = (
    METADATA,
    PROVIDERS,
    MODELS,
    MODEL_MODALITIES,
    EXPERIMENTAL_MODES,
    PRICING,
)

PROVIDER_COLUMNS = _column_list(PROVIDERS)
MODEL_COLUMNS = _column_list(MODELS)
PROVIDER_INSERT_SQL = _insert_sql(PROVIDERS)
MODEL_INSERT_SQL = _insert_sql(MODELS)
MODEL_MODALITY_INSERT_SQL = _insert_sql(MODEL_MODALITIES)
EXPERIMENTAL_MODE_INSERT_SQL = _insert_sql(EXPERIMENTAL_MODES)
PRICING_INSERT_SQL = _insert_sql(PRICING)
CREATE_SCHEMA_SQL = _schema_sql(TABLES)
