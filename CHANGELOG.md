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
  on writes, and a flat JSONL export format that carries records,
  suggestions, and responses; import reloads the records and suggestions and
  keeps the responses out of resubmission.
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

## [Unreleased]

### Fixed

- Credentials resolve as a set, not per field: a URL from a flag or the
  environment no longer borrows a secret stored for another server's account.
- `users create --username/--password` no longer leak into the invocation's
  own authentication; the command keeps using the caller's account.
- The HTTP client refuses redirects (they could forward secrets), rejects
  non-JSON answers with a clean error, and never quotes response bodies.
- `datasets update --settings` applies the dump to the named dataset only;
  a dump taken from another dataset is rebuilt against the target instead of
  pointing at the source.
- `datasets export` and `responses export` write atomically and refuse
  reserved name collisions on import.
- `suggestions add` validates every row before writing and upserts through
  the dataset's record log.
- The config file is written atomically (temp file plus rename) and keeps
  0600 permissions; removing the active account clears the pointer.
- Global `--yes` skips confirmations, `--json` also works after the
  subcommand, `--profile read-only` can only tighten, and `login` without
  `--account` saves to a derived name instead of overwriting the active
  account.
- `responses submit` no longer offers `--user`: the 2.8 server pins every
  response to the authenticated user, so the flag could not be honored.

### Changed

- `pip-audit` in CI audits the project's own locked dependencies instead of
  the audit tool's environment.
