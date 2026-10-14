"""Shared helpers for commands that drive the official SDK.

Commands stay thin: resolve credentials once per invocation, build the SDK
client once, find resources with clean errors, and map SDK failures to one
redacted CLI message.
"""

import warnings
from collections.abc import Callable
from typing import Any, TypeVar

import click
from click.decorators import FC

from argilla_cli import auth, masking
from argilla_cli.errors import ArgillaCLIError, CredentialsError, NotFoundError

T = TypeVar("T")

# The SDK resource objects are duck-typed here: they come from the official
# client built in get_client(), and unit tests replace it with a fake.
ArgillaClient = Any
Resource = Any


def get_credentials(ctx: click.Context | None) -> auth.Credentials:
    """Resolve the credentials once per invocation and cache them."""
    creds = (ctx.obj or {}).get("credentials") if ctx else None
    if creds is None:
        creds = auth.resolve_credentials(ctx)
        if ctx is not None and ctx.obj is not None:
            ctx.obj["credentials"] = creds
    return creds


def profile_in_force(ctx: click.Context | None) -> str:
    """The permission profile for this invocation: flag override or the account's."""
    return get_credentials(ctx).profile


def get_client(ctx: click.Context | None) -> ArgillaClient:
    """Build the SDK client once per invocation from the resolved credentials."""
    client = (ctx.obj or {}).get("client") if ctx else None
    if client is not None:
        return client
    creds = get_credentials(ctx)
    if not creds.url:
        raise CredentialsError("No server URL. Run `argilla-cli login` or pass --url.")
    client = auth.build_client(creds)
    if ctx is not None and ctx.obj is not None:
        ctx.obj["client"] = client
    return client


def quiet_call(action: Callable[[], T]) -> T | None:
    """Run an SDK lookup, hiding its not-found UserWarning: we report our own."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return action()


def find_workspace(client: ArgillaClient, name: str) -> Resource:
    workspace = quiet_call(lambda: client.workspaces(name))
    if workspace is None:
        raise NotFoundError(f"No workspace named {name!r} on this server.")
    return workspace


def find_user(client: ArgillaClient, username: str) -> Resource:
    user = quiet_call(lambda: client.users(username=username))
    if user is None:
        raise NotFoundError(f"No user named {username!r} on this server.")
    return user


def find_dataset(client: ArgillaClient, name: str, workspace: Resource | None = None) -> Resource:
    if workspace is not None:
        dataset = quiet_call(lambda: client.datasets(name, workspace=workspace))
        if dataset is None:
            raise NotFoundError(f"No dataset named {name!r} in workspace {workspace.name!r}.")
        return dataset
    dataset = quiet_call(lambda: client.datasets(name))
    if dataset is not None:
        return dataset
    # The default workspace may not hold the dataset: scan the server.
    matches = [model for model in client.api.datasets.list() if model.name == name]
    if len(matches) > 1:
        raise NotFoundError(
            f"Several datasets named {name!r} exist in different workspaces. Pass --workspace to pick one."
        )
    if matches:
        found = quiet_call(lambda: client.datasets(id=matches[0].id))
        if found is not None:
            return found
    raise NotFoundError(f"No dataset named {name!r} on this server.")


def default_workspace(client: ArgillaClient) -> Resource:
    workspace = quiet_call(lambda: client.workspaces.default)
    if workspace is None:
        raise NotFoundError("No default workspace on this server. Create one with `workspaces create`.")
    return workspace


def sdk_call(action: Callable[[], T], what: str) -> T:
    """Run a state-changing SDK call, mapping failures to one clean message."""
    try:
        return action()
    except Exception as exc:  # SDK and httpx failures alike become CLI errors
        raise ArgillaCLIError(f"{what} failed: {masking.redact_message(str(exc))}") from exc


def with_account(command: FC) -> FC:
    """Attach the per-command --account flag, as the plan asks for every command."""
    return click.option("--account", "account", default=None, help="Use this named account.")(command)
