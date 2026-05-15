# modelsdotdev-python

An offline [models.dev](https://models.dev) database bundle exposed as a
Python module.

```python
from modelsdotdev import get_model_by_id, get_provider_by_name

provider = get_provider_by_name("OpenAI")
model = get_model_by_id("openai:gpt-5.5")

if provider is not None:
    provider_model = provider.get_model_by_id("gpt-5.5")
    provider_models = list(provider.iter_models())
```

## Development

Install dependencies and run the test suite with uv:

```sh
uv run pytest
```

Running the test suite refreshes the database used by tests. Editable installs
refresh the in-tree SQLite database; other installs use a temporary database
path exposed through `MODELDOTDEV_DATABASE_PATH`. You can also refresh the
in-tree database explicitly with the Poe task:

```sh
uv run poe generate-db
```

Source checkouts do not generate the database during normal imports or editable
installs. Distribution builds generate it automatically if it is missing so
published artifacts remain self-contained.

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE) for
details.
