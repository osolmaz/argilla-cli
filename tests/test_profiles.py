"""Tests for permission profiles and destructive confirmation."""

import pytest

from argilla_cli import profiles
from argilla_cli.errors import ArgillaCLIError, ProfileDeniedError


@pytest.mark.parametrize("path", sorted(profiles.WRITE_COMMANDS))
def test_full_profile_allows_writes(path):
    profiles.enforce_profile(path, "full")


@pytest.mark.parametrize("path", sorted(profiles.WRITE_COMMANDS))
def test_read_only_refuses_every_write(path):
    with pytest.raises(ProfileDeniedError, match="refuses every write"):
        profiles.enforce_profile(path, "read-only")


@pytest.mark.parametrize(
    "path", ["whoami", "info", "datasets list", "records list", "responses export", "datasets export"]
)
def test_read_only_allows_reads(path):
    profiles.enforce_profile(path, "read-only")


def test_unknown_profile_is_an_error():
    with pytest.raises(ArgillaCLIError, match="Unknown profile"):
        profiles.enforce_profile("datasets list", "admin")


def test_confirm_destructive_needs_yes_without_terminal(capsys):
    for path in sorted(profiles.DESTRUCTIVE_COMMANDS):
        with pytest.raises(ArgillaCLIError, match="--yes"):
            profiles.confirm_destructive(path, yes=False)
        profiles.confirm_destructive(path, yes=True)


def test_non_destructive_never_confirms():
    profiles.confirm_destructive("datasets create", yes=False)


def test_delete_and_remove_user_paths_are_destructive():
    expected = {
        "workspaces delete",
        "workspaces remove-user",
        "users delete",
        "datasets delete",
        "records delete",
    }
    assert expected == profiles.DESTRUCTIVE_COMMANDS
