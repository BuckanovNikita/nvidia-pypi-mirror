# NVIDIA PyPI mirror

This repository ships a standalone Python 3.12+ script with no runtime dependencies.
Keep the script directly runnable as well as installable through its console entrypoint.
Use uv with pyproject.toml and uv.lock for development.

Preserve artifact hashes, HTML link metadata, and source-relative URL paths. Never
download package binaries during index mirroring. Small mirrors must include only
the requested projects in the root listing. Publish local output only after all
requested downloads succeed, and preserve pre-existing output directories.

Before committing Python or configuration changes, run:

```sh
uv lock --check
uv run --frozen ruff check .
uv run --frozen ruff format --check .
uv run --frozen mypy
uv run --frozen pytest
uv build
uv run --frozen mirror-nvidia-pypi --help
```

Keep the README in Russian. Other documentation and code comments use English.
Do not commit generated mirrors, credentials, local paths, or machine configuration.
