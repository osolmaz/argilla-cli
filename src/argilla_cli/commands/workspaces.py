"""workspaces: list, create, delete, add-user, remove-user."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import click

if TYPE_CHECKING:
    from argilla import Workspace

from argilla_cli.commands.helpers import (
    confirm_destructive,
    find_user,
    find_workspace,
    get_client,
    profile_in_force,
    quiet_call,
    sdk_call,
    with_account,
)
from argilla_cli.errors import ArgillaCLIError
from argilla_cli.output import emit
from argilla_cli.profiles import enforce_profile


def _workspace_row(workspace: Workspace) -> dict[str, Any]:
    return {
        "id": str(workspace.id) if workspace.id else None,
        "name": workspace.name,
        "inserted_at": str(workspace.inserted_at) if workspace.inserted_at else None,
        "updated_at": str(workspace.updated_at) if workspace.updated_at else None,
    }


@click.group("workspaces")
def workspaces() -> None:
    """Manage workspaces."""


@workspaces.command("list")
@with_account
@click.pass_context
def workspaces_list(ctx: click.Context, account: str | None) -> None:
    """List every workspace on the server."""
    client = get_client(ctx)
    rows = [_workspace_row(ws) for ws in client.workspaces]
    emit(ctx, rows)


@workspaces.command("create")
@click.argument("name")
@click.option("--dry-run", is_flag=True, default=False, help="Show what would be created without creating it.")
@with_account
@click.pass_context
def workspaces_create(ctx: click.Context, name: str, dry_run: bool, account: str | None) -> None:
    """Create a workspace named NAME."""
    enforce_profile("workspaces create", profile_in_force(ctx))
    client = get_client(ctx)
    if quiet_call(lambda: client.workspaces(name)) is not None:
        raise ArgillaCLIError(f"A workspace named {name!r} already exists.")
    if dry_run:
        emit(ctx, {"action": "create", "resource": "workspace", "name": name, "dry_run": True})
        return
    import argilla as rg

    workspace = sdk_call(lambda: rg.Workspace(name=name, client=client).create(), "Creating workspace")
    emit(ctx, _workspace_row(workspace))


@workspaces.command("delete")
@click.argument("name")
@click.option("--yes", "-y", is_flag=True, default=False, help="Skip the confirmation prompt.")
@with_account
@click.pass_context
def workspaces_delete(ctx: click.Context, name: str, yes: bool, account: str | None) -> None:
    """Delete the workspace named NAME."""
    enforce_profile("workspaces delete", profile_in_force(ctx))
    confirm_destructive(ctx, "workspaces delete", yes=yes, details=name)
    client = get_client(ctx)
    workspace = find_workspace(client, name)
    sdk_call(lambda: workspace.delete(), "Deleting workspace")
    emit(ctx, {"action": "delete", "resource": "workspace", "name": name})


@workspaces.command("add-user")
@click.argument("workspace_name")
@click.argument("username")
@with_account
@click.pass_context
def workspaces_add_user(ctx: click.Context, workspace_name: str, username: str, account: str | None) -> None:
    """Add the user USERNAME to the workspace WORKSPACE_NAME."""
    enforce_profile("workspaces add-user", profile_in_force(ctx))
    client = get_client(ctx)
    workspace = find_workspace(client, workspace_name)
    find_user(client, username)
    sdk_call(lambda: workspace.users.add(username), "Adding user to workspace")
    emit(ctx, {"action": "add-user", "workspace": workspace_name, "username": username})


@workspaces.command("remove-user")
@click.argument("workspace_name")
@click.argument("username")
@click.option("--yes", "-y", is_flag=True, default=False, help="Skip the confirmation prompt.")
@with_account
@click.pass_context
def workspaces_remove_user(
    ctx: click.Context, workspace_name: str, username: str, yes: bool, account: str | None
) -> None:
    """Remove the user USERNAME from the workspace WORKSPACE_NAME."""
    enforce_profile("workspaces remove-user", profile_in_force(ctx))
    confirm_destructive(ctx, "workspaces remove-user", yes=yes, details=f"{username} from {workspace_name}")
    client = get_client(ctx)
    workspace = find_workspace(client, workspace_name)
    find_user(client, username)
    sdk_call(lambda: workspace.users.delete(username), "Removing user from workspace")
    emit(ctx, {"action": "remove-user", "workspace": workspace_name, "username": username})


__all__ = ["_workspace_row", "workspaces"]
