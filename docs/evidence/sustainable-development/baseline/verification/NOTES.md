# Baseline verification notes

Archived at starting commit `3255853cf17d8ef03bc0f0a6b773f92a65b3c076`:

| Command | Result | Artifact |
| --- | --- | --- |
| `uv run ruff format --check src tests` | pass (358 files) | `ruff-format.txt` |
| `uv run ruff check src tests` | pass | `ruff-check.txt` |
| `uv run basedpyright` | 0 errors | `basedpyright.txt` |

Deferred to SD0/G0 re-run (large / environment-heavy; not required to freeze the SHA):

- `uv run pytest -q -m "not integration"`
- `npm --prefix dashboard test/check/build`
- `npm --prefix integrations/opencode-plugin test/check`
- `uv build`

G0 evidence will link the full ladder plus targeted security/migration suites.
