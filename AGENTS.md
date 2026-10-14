# AGENTS.md

Instructions for coding agents working in this repository.

## Commands

```sh
uv sync --dev                    # install with dev tools (creates .venv/)
uv run pytest                    # unit tests; integration tests are excluded by default
uv run pytest -m integration     # end-to-end tests against a Dockerized Argilla server
uv run ruff check .              # lint
uv run ruff format .             # format
uv run mypy                      # strict typecheck of src/
uv run pytest --cov --cov-fail-under=85   # tests with the coverage gate
uvx pip-audit --strict           # dependency audit
uvx slophammer-py dry .          # duplication check
uv run python scripts/check-mutation.py --min-kill-rate 70   # mutation gate
uvx slophammer-py check .        # Slophammer conformance
```

## Rules

- Run `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .`,
  and `uv run mypy` before you finish any change.
- Keep type annotations on every public function and helper in `src/`; mypy
  runs with `strict = true`.
- Never weaken a quality gate to make a change pass: raise the code, not the
  threshold.
- Never print, log, commit, or test against a real password, token, or API
  key. Secret values never appear in output, errors, fixtures, or files; the
  read-only smoke test against the studio is the only allowed live-server test
  and must go through `--credentials-file`.
- Tests must not touch the network: `tests/conftest.py` blocks real HTTP
  requests; use the `mock_http` fixture or SDK fakes.
- Every CLI command prints JSON with `--json` and exits non-zero on failure.
- Writes go through `profiles.enforce_profile` before any confirmation prompt;
  destructive commands ask for confirmation and need `--yes` without a
  terminal.
- Commit messages follow Conventional Commits.
- This repository applies Slophammer standards; see
  https://github.com/osolmaz/slophammer/blob/main/docs/AGENT_ENTRYPOINT.md.
