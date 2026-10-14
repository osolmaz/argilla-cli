"""users: list, get, create, delete."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import click

if TYPE_CHECKING:
    from argilla import Role, User

from argilla_cli.commands.helpers import (
    find_user,
    get_client,
    profile_in_force,
    sdk_call,
    with_account,
)
from argilla_cli.masking import register_secrets
from argilla_cli.output import emit
from argilla_cli.profiles import confirm_destructive, enforce_profile

ROLE_CHOICES = ("owner", "admin", "annotator")


def _role_label(role: Role | str | None) -> str | None:
    """Render the SDK's Role enum as its plain name."""
    if role is None:
        return None
    return str(getattr(role, "value", role))


def _user_row(user: User) -> dict[str, Any]:
    return {
        "id": str(user.id) if user.id else None,
        "username": user.username,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "role": _role_label(user.role),
    }


@click.group("users")
def users() -> None:
    """Manage users."""


@users.command("list")
@with_account
@click.pass_context
def users_list(ctx: click.Context, account: str | None) -> None:
    """List every user on the server."""
    client = get_client(ctx)
    rows = [_user_row(user) for user in client.users]
    emit(ctx, rows)


@users.command("get")
@click.argument("username")
@with_account
@click.pass_context
def users_get(ctx: click.Context, username: str, account: str | None) -> None:
    """Show the user named USERNAME."""
    client = get_client(ctx)
    user = find_user(client, username)
    emit(ctx, _user_row(user))


@users.command("create")
@click.option("--username", required=True, help="The login name for the new user.")
@click.option("--password", default=None, help="The new user's password. Asked interactively when omitted.")
@click.option("--first-name", default=None, help="Optional first name.")
@click.option("--last-name", default=None, help="Optional last name.")
@click.option("--role", type=click.Choice(ROLE_CHOICES, case_sensitive=False), default="annotator", show_default=True)
@click.option("--dry-run", is_flag=True, default=False, help="Show what would be created without creating it.")
@with_account
@click.pass_context
def users_create(
    ctx: click.Context,
    username: str,
    password: str | None,
    first_name: str | None,
    last_name: str | None,
    role: str,
    dry_run: bool,
    account: str | None,
) -> None:
    """Create a user."""
    enforce_profile("users create", profile_in_force(ctx))
    if password is None:
        password = click.prompt(f"Password for {username}", hide_input=True)
    register_secrets([password])
    client = get_client(ctx)
    if dry_run:
        emit(
            ctx,
            {
                "action": "create",
                "resource": "user",
                "username": username,
                "role": role.lower(),
                "first_name": first_name,
                "last_name": last_name,
                "dry_run": True,
            },
        )
        return
    import argilla as rg

    user = sdk_call(
        lambda: rg.User(
            username=username,
            password=password,
            role=role.lower(),
            first_name=first_name,
            last_name=last_name,
            client=client,
        ).create(),
        "Creating user",
    )
    emit(ctx, _user_row(user))


@users.command("delete")
@click.argument("username")
@click.option("--yes", is_flag=True, default=False, help="Skip the confirmation prompt.")
@with_account
@click.pass_context
def users_delete(ctx: click.Context, username: str, yes: bool, account: str | None) -> None:
    """Delete the user named USERNAME."""
    enforce_profile("users delete", profile_in_force(ctx))
    confirm_destructive("users delete", yes=yes, details=username)
    client = get_client(ctx)
    user = find_user(client, username)
    sdk_call(lambda: user.delete(), "Deleting user")
    emit(ctx, {"action": "delete", "resource": "user", "username": username})


__all__ = ["_user_row", "users"]
