# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-10-14

### Added

- Login with an API key or a username and password; at every resolution level
  (flags, environment, saved account, credentials file) an API key wins over a
  password. The server's API key is kept in memory only, never saved.
- `setup`, `whoami`, `info`, and `doctor`, with the login method named and
  secrets masked.
- Named accounts as in kubectl contexts: `account list | use | remove`,
  `config show`, and `--account` on every command.
- `workspaces list | create | delete | add-user | remove-user`.
- `users list | get | create | delete`.
- `datasets list | get | create | update | delete | progress | export | import`,
  with a settings dump as JSON or YAML, `--like` to copy settings, `--dry-run`
  on writes, and a flat JSONL export/import format that carries records,
  suggestions, and responses.
- `records list | get | add | update | delete`, reading JSON or JSONL and
  upserting by `id`.
- `responses list | export | submit` and `suggestions add`, both by question
  name.
- Safety rails: destructive commands confirm and need `--yes` without a
  terminal, secrets never appear in output or errors, and each account has a
  `full` or `read-only` permission profile.
- Integration tests against a digest-pinned `argilla/argilla-server:v2.8.0`
  Docker stack (opt-in with `ARGILLA_CLI_INTEGRATION=1`), and a read-only
  smoke test against a live studio (opt-in with `ARGILLA_CLI_STUDIO=1`).
