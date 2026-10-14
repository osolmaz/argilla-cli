"""Permission profiles, as in discli.

Each account carries a profile: `full` (the default) or `read-only`, which
refuses every write. A `--profile` flag can tighten or loosen the check for a
single invocation. Writes are refused before any confirmation prompt, so a
denied command never asks "are you sure?" first.
"""

import sys

import click

from argilla_cli.errors import ArgillaCLIError, ProfileDenied

PROFILES = {
    "full": "Full access to every command.",
    "read-only": "Read-only: every write is refused.",
}

# Command paths that change server state. The read-only profile refuses these.
WRITE_COMMANDS = frozenset({
    "workspaces create",
    "workspaces delete",
    "workspaces add-user",
    "workspaces remove-user",
    "users create",
    "users delete",
    "datasets create",
    "datasets update",
    "datasets delete",
    "datasets import",
    "records add",
    "records update",
    "records delete",
    "responses submit",
    "suggestions add",
})

# Destructive commands ask for confirmation and need --yes without a terminal.
DESTRUCTIVE_COMMANDS = frozenset({
    "workspaces delete",
    "workspaces remove-user",
    "users delete",
    "datasets delete",
    "records delete",
})


def is_write(command_path: str) -> bool:
    return command_path in WRITE_COMMANDS


def enforce_profile(command_path: str, profile: str) -> None:
    """Refuse a write when the profile in force is read-only."""
    if profile not in PROFILES:
        raise ArgillaCLIError(
            f"Unknown profile {profile!r}. Known profiles: {', '.join(PROFILES)}."
        )
    if profile == "read-only" and is_write(command_path):
        raise ProfileDenied(
            f"'{command_path}' is a write and the '{profile}' profile refuses every write. "
            "Run it with an account whose profile is 'full', or with --profile full."
        )


def confirm_destructive(command_path: str, yes: bool, details: str = "") -> None:
    """Ask before a destructive command; require --yes when there is no terminal.

    Callers run the profile check first: a command the profile forbids must
    not get as far as a confirmation prompt.
    """
    if command_path not in DESTRUCTIVE_COMMANDS:
        return
    if yes:
        return
    if not sys.stdin.isatty():
        raise ArgillaCLIError(
            f"'{command_path}' is destructive and there is no terminal to confirm it. Re-run with --yes."
        )
    message = f"Destructive action: {command_path}"
    if details:
        message += f" ({details})"
    click.confirm(message + ". Continue?", default=False, abort=True)
