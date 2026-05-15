import sqlite3
from contextlib import closing

import pytest

from modelsdotdev import (
    Capability,
    Cost,
    ExperimentalMode,
    Limits,
    Modalities,
    Modality,
    Model,
    ModelProviderConfig,
    ModelRef,
    Provider,
    ProviderAPIShape,
    Status,
    get_model_by_id,
    get_provider_by_id,
    get_provider_by_name,
    iter_models,
    iter_providers,
    parse_model_id,
)
from modelsdotdev._internal.data import DB_PATH


def test_provider_iteration_and_lookup_use_real_database() -> None:
    providers = list(iter_providers())
    assert providers
    assert providers == sorted(
        providers,
        key=lambda provider: provider.name.lower(),
    )
    assert len({provider.id for provider in providers}) == len(providers)
    assert len({provider.name.lower() for provider in providers}) == len(
        providers,
    )

    provider = providers[0]
    assert isinstance(provider, Provider)
    assert get_provider_by_id(provider.id) == provider
    assert get_provider_by_name(provider.name) == provider
    assert get_provider_by_name(provider.name.upper()) == provider
    assert get_provider_by_id("missing-provider") is None
    assert get_provider_by_name("missing provider") is None


def test_model_iteration_and_lookup_use_real_database() -> None:
    providers_by_id = {provider.id: provider for provider in iter_providers()}
    models = list(iter_models())
    assert models
    assert models == sorted(models, key=lambda model: model.qualified_id)
    assert len({model.qualified_id for model in models}) == len(models)

    for model in models:
        assert isinstance(model, Model)
        assert model.provider_id in providers_by_id
        assert model.qualified_id == f"{model.provider_id}:{model.id}"
        assert get_model_by_id(model.qualified_id) == model
        assert get_model_by_id(f"{model.provider_id}/{model.id}") == model
        assert (
            providers_by_id[model.provider_id].get_model_by_id(model.id)
            == model
        )
        _assert_model_shape(model)

    assert get_model_by_id("missing:model") is None
    with pytest.raises(ValueError, match="provider:model"):
        get_model_by_id(models[0].id)


def test_provider_model_iteration_matches_global_models() -> None:
    models_by_provider: dict[str, list[Model]] = {}
    for model in iter_models():
        models_by_provider.setdefault(model.provider_id, []).append(model)

    for provider in iter_providers():
        provider_models = list(provider.iter_models())
        assert provider_models == sorted(
            provider_models,
            key=lambda model: model.id,
        )
        assert provider_models == models_by_provider.get(provider.id, [])


def test_parse_model_id_handles_qualified_and_unqualified_ids() -> None:
    assert parse_model_id("openai:gpt-5") == ModelRef(
        provider_id="openai",
        vendor_id=None,
        model_id="gpt-5",
    )
    assert parse_model_id("openrouter/openai/gpt-oss-120b") == ModelRef(
        provider_id="openrouter",
        vendor_id=None,
        model_id="openai/gpt-oss-120b",
    )
    assert parse_model_id("amazon-bedrock:amazon.nova-pro-v1:0") == ModelRef(
        provider_id="amazon-bedrock",
        vendor_id=None,
        model_id="amazon.nova-pro-v1:0",
    )
    bedrock_model_id = "anthropic.claude-opus-4-5-20251101-v1:0"
    assert parse_model_id(bedrock_model_id) == ModelRef(
        provider_id=None,
        vendor_id=None,
        model_id=bedrock_model_id,
    )


@pytest.mark.parametrize(
    "model_id",
    ["", ":gpt-5", "openai:", "/gpt-5", "openai/"],
)
def test_parse_model_id_rejects_empty_parts(model_id: str) -> None:
    with pytest.raises(ValueError, match="model_id"):
        parse_model_id(model_id)


def test_real_database_contains_rich_model_metadata() -> None:
    models = list(iter_models())
    assert any(model.cost is not None for model in models)
    assert any(model.family is not None for model in models)
    assert any(model.experimental_modes is not None for model in models)
    assert any(model.interleaved_field is not None for model in models)
    assert any(model.provider_config is not None for model in models)
    assert any(model.status is not None for model in models)


def test_real_database_uses_normalized_pricing_table() -> None:
    with closing(sqlite3.connect(DB_PATH)) as connection:
        model_columns = _table_columns(connection, "models")
        mode_columns = _table_columns(connection, "experimental_modes")
        pricing_columns = _table_columns(connection, "pricing")
        pricing_count = connection.execute(
            "SELECT count(*) FROM pricing",
        ).fetchone()[0]

    assert not any(column.startswith("cost_") for column in model_columns)
    assert not any(column.startswith("cost_") for column in mode_columns)
    assert {
        "model_full_id",
        "experimental_mode_name",
        "min_context",
        "cost_input",
        "cost_output",
    } <= pricing_columns
    assert pricing_count > 0


def _assert_model_shape(model: Model) -> None:
    assert model.id
    assert model.name
    assert isinstance(model.capabilities, frozenset)
    assert all(
        isinstance(capability, Capability) for capability in model.capabilities
    )
    assert isinstance(model.modalities, Modalities)
    assert model.modalities.input
    assert model.modalities.output
    assert all(
        isinstance(modality, Modality) for modality in model.modalities.input
    )
    assert all(
        isinstance(modality, Modality) for modality in model.modalities.output
    )
    assert isinstance(model.open_weights, bool)
    assert isinstance(model.limits, Limits)
    assert model.limits.context >= 0
    assert model.limits.output >= 0
    assert model.limits.input is None or model.limits.input >= 0
    assert model.status is None or isinstance(model.status, Status)

    if model.cost is not None:
        _assert_costs(model.cost)
    if model.interleaved_field is not None:
        _assert_interleaved_field(model.interleaved_field)
    if model.experimental_modes is not None:
        _assert_experimental_modes(model.experimental_modes)
    if model.provider_config is not None:
        _assert_provider_config(model.provider_config)


def _assert_costs(costs: list[Cost]) -> None:
    assert costs
    assert [cost.min_context for cost in costs] == sorted(
        cost.min_context for cost in costs
    )
    for cost in costs:
        _assert_cost(cost)
    assert len({_pricing_key(cost) for cost in costs}) == len(costs)


def _assert_cost(cost: Cost) -> None:
    assert cost.min_context >= 0
    assert cost.input >= 0
    assert cost.output >= 0
    assert cost.reasoning is None or cost.reasoning >= 0
    assert cost.cache_read is None or cost.cache_read >= 0
    assert cost.cache_write is None or cost.cache_write >= 0
    assert cost.input_audio is None or cost.input_audio >= 0
    assert cost.output_audio is None or cost.output_audio >= 0


def _pricing_key(
    cost: Cost,
) -> tuple[
    float,
    float,
    float | None,
    float | None,
    float | None,
    float | None,
    float | None,
]:
    return (
        cost.input,
        cost.output,
        cost.reasoning,
        cost.cache_read,
        cost.cache_write,
        cost.input_audio,
        cost.output_audio,
    )


def _table_columns(
    connection: sqlite3.Connection,
    table: str,
) -> set[str]:
    return {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}


def _assert_interleaved_field(interleaved_field: str) -> None:
    assert isinstance(interleaved_field, str)


def _assert_experimental_modes(
    experimental_modes: dict[str, ExperimentalMode],
) -> None:
    assert experimental_modes
    for name, mode in experimental_modes.items():
        assert name
        if mode.cost is not None:
            _assert_costs(mode.cost)
        if mode.provider is not None:
            _assert_provider_config(mode.provider)


def _assert_provider_config(config: ModelProviderConfig) -> None:
    assert any(
        value is not None
        for value in (
            config.npm,
            config.api,
            config.api_shape,
            config.body,
            config.headers,
        )
    )
    assert config.api_shape is None or isinstance(
        config.api_shape,
        ProviderAPIShape,
    )
    if config.body is not None:
        assert isinstance(config.body, dict)
    if config.headers is not None:
        assert isinstance(config.headers, dict)
        assert all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in config.headers.items()
        )
