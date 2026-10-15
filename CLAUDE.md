# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

argilla-cli is a command-line client for Argilla 2.x servers, for people and agents. It wraps the official `argilla` SDK (pinned `==2.8.0`) with a click CLI: accounts and login, workspaces, users, datasets, records, responses, and suggestions. Every command takes `--json`; without it, output is a short table.

## Commands

This project uses [uv](https://docs.astral.sh/uv/) for dependency management.

```bash
# Install (editable, with dev deps) — creates .venv/ automatically
uv sync --dev

# Run tests
uv run pytest -q

# Run a single test file
uv run pytest tests/test_login_flow.py -v

# Run the CLI
uv run argilla-cli --help

# Lint, format, typecheck (all strict)
uv run ruff check .
uv run ruff format .
uv run mypy

# Coverage gate (85) and mutation gate (kill rate floor in CI)
uv run pytest --cov --cov-fail-under=85
uv run python scripts/check-mutation.py --min-kill-rate 65

# Slophammer conformance (duplication + tooling rules)
uvx --python 3.13 slophammer-py@0.4.0 dry .
uvx --python 3.13 slophammer-py@0.4.0 check .

# Dependency audit (the project's locked dependencies, without the project itself)
uv export --no-hashes --format requirements-txt --output-file requirements-audit.txt
grep -v '^-e ' requirements-audit.txt > requirements-audit.filtered
uvx pip-audit --strict -r requirements-audit.filtered
```

The mutation gate runs mutmut under `mutants/` (gitignored). It is a build cache: delete `mutants/` when test code changed but source did not, or mutmut replays stale results. slophammer-py needs Python >= 3.12, hence `--python 3.13` while the project supports 3.10.

## Architecture

- `src/argilla_cli/cli.py` — the click group and global flags (`--json`, `--yes`, `--profile`, `--account`, credential flags). `main_entry()` is the console script: one redacted error line, never a traceback.
- `src/argilla_cli/auth.py` — credential resolution (flags → env → account/credentials file; an API key wins at every level), raw token/`me`/version HTTP, and `build_client()` for the SDK client.
- `src/argilla_cli/config.py` — the 0700/0600 config at `~/.argilla-cli/config.json` (overridable with `ARGILLA_CLI_CONFIG`).
- `src/argilla_cli/masking.py` — the secret registry and `redact_message()`; every error path funnels through it.
- `src/argilla_cli/profiles.py` — `full`/`read-only` profiles, destructive-command confirmation.
- `src/argilla_cli/output.py` — `emit()`: the only place a command prints.
- `src/argilla_cli/commands/` — one module per command family; `helpers.py` builds the SDK client once per invocation and maps SDK failures to clean CLI errors.

## Testing

- `tests/conftest.py` holds autouse fixtures: `no_network` (blocks real HTTP except for marked tests), `clean_secrets`, `isolated_config` (`ARGILLA_CLI_CONFIG` → tmp), plus `fake_ctx` (an isolated config with one API-key account and the `tests/fakes.py` in-memory client standing in for the SDK client).
- Command tests drive the REAL `argilla` resource classes over that fake client — no network. `tests/test_integration.py` (marker `integration`, opt-in with `ARGILLA_CLI_INTEGRATION=1`) runs the official `argilla/argilla-server:v2.8.0` stack in Docker via Compose; the server image is resolved to its digest at run time. `tests/test_studio_smoke.py` (marker `studio`, opt-in with `ARGILLA_CLI_STUDIO=1`) is a read-only smoke test against the studio with `--credentials-file`.
- Integration and studio tests are excluded by default (`addopts` in `pyproject.toml`).

## Conventions

- Commit style: conventional commits (`feat:`, `fix:`, `docs:`, `chore:`).
- The package command is `argilla-cli`, never `argilla`.
- Never print or persist a secret outside the 0600 config; error paths redact, and test assertions check it for both login methods.
