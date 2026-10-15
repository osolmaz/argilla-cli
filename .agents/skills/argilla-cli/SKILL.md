---
name: argilla-cli
description: "Drive Argilla 2.x servers from the terminal with argilla-cli. Log in once, then list and create workspaces, users, datasets, records, responses, and suggestions. Use --json for parseable output; every command exits non-zero on failure."
---

# Argilla CLI

`argilla-cli` is a client for Argilla 2.x review servers (argilla.io). Use it to inspect and edit annotation datasets: workspaces, users, datasets, records, responses, and suggestions.

## Setup

Check for an active account first:

```bash
argilla-cli --json account list   # saved accounts, * marks the active one
argilla-cli --json whoami         # errors if nothing is active
```

Log in when needed (the account persists in `~/.argilla-cli/config.json`):

```bash
argilla-cli login --url https://argilla.example.org --api-key "$ARGILLA_API_KEY"
# or: --username amelie (prompts for the password if not given)
# or: --credentials-file /path/to/env (holds URL/API_KEY or URL/USERNAME/PASSWORD)
```

One-off credentials without saving: pass the same flags to any command, or set
`ARGILLA_API_URL` and `ARGILLA_API_KEY` (or `ARGILLA_USERNAME`/`ARGILLA_PASSWORD`) in the environment.

## Reading

Always pass `--json` when another program or agent consumes the output; without it, tables are for humans. Every command exits non-zero on failure.

```bash
argilla-cli --json workspaces list
argilla-cli --json datasets list --workspace review
argilla-cli --json datasets get "Added criteria review: Kein Review follow-up" --settings --format yaml
argilla-cli --json datasets progress "Added criteria review: Kein Review follow-up"
argilla-cli --json records list "Added criteria review: Kein Review follow-up" --limit 20
argilla-cli --json records get "Added criteria review: Kein Review follow-up" 8bd4...
argilla-cli --json responses list "Added criteria review: Kein Review follow-up" --user amelie
```

Dataset names with spaces or `:` need shell quoting. To reuse a dataset's format, dump its settings with `datasets get --settings`.

## Writing

Writes need a `full`-profile account; a `read-only` profile refuses them.

```bash
# Create a dataset from a settings file (JSON or YAML: fields, questions, ...);
# --dry-run prints the plan and writes nothing
argilla-cli --json datasets create "My review" --workspace review --settings settings.json

# Copy the settings of an existing dataset
argilla-cli --json datasets create "My review" --like "Added criteria review: Kein Review follow-up"

# Add records from JSON/JSONL; lines are flat: {"id": "r1", "<field>": ...};
# an existing id is upserted, not duplicated
argilla-cli --json records add "My review" --file records.jsonl

# Submit an answer to a record's questions (keys are question names)
argilla-cli --json responses submit "My review" r1 --answers '{"quality": "ok"}'

# Attach suggestions (draft answers) from a file
argilla-cli --json suggestions add "My review" --file suggestions.jsonl

# Export everything as flat JSONL (suggestions flattened, responses kept nested)
argilla-cli --json datasets export "My review" -o export.jsonl
# Re-import into a fresh dataset (responses are not resubmitted)
argilla-cli --json datasets import "My review 2" export.jsonl
```

Destructive commands (`delete`, `remove-user`) ask for confirmation; non-interactive runs need `--yes`. Prefer `--dry-run` first when a create or import is untested.

## Notes

- Secrets never appear in output or errors; `config show` masks them.
- `doctor` diagnoses a server: reachability, login, version match with the bundled SDK, and the account's role.
- `--account NAME` or `--profile read-only` overrides the active account for one invocation.
