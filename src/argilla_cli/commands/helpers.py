"""Shared helpers for commands that drive the official SDK.

Commands stay thin: resolve credentials once per invocation, build the SDK
client once, find resources with clean errors, and map SDK failures to one
redacted CLI message.
"""

import functools
import warnings
from collections.abc import Callable
from typing import Any, TypeVar, cast

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
    """Find a dataset by name, never guessing between same-named copies."""
    if workspace is not None:
        dataset = quiet_call(lambda: client.datasets(name, workspace=workspace))
        if dataset is None:
            raise NotFoundError(f"No dataset named {name!r} in workspace {workspace.name!r}.")
        return dataset
    # Scan every workspace: the SDK's by-name lookup only searches the first
    # one, so a same-named dataset elsewhere would stay invisible or win by
    # accident.
    matches = [model for model in client.api.datasets.list() if model.name == name]
    if len(matches) > 1:
        workspace_names = _workspace_names(client)
        found = sorted(workspace_names.get(str(model.workspace_id), "?") for model in matches)
        raise NotFoundError(
            f"Several datasets named {name!r} exist (workspaces: {', '.join(found)}). Pass --workspace to pick one."
        )
    if matches:
        found_dataset = quiet_call(lambda: client.datasets(id=matches[0].id))
        if found_dataset is not None:
            return found_dataset
    raise NotFoundError(f"No dataset named {name!r} on this server.")


def _workspace_names(client: ArgillaClient) -> dict[str, str]:
    return {str(ws.id): ws.name for ws in client.workspaces}


def with_workspace(command: FC) -> FC:
    """Attach the --workspace scoping flag for commands that take a dataset."""
    return click.option(
        "--workspace",
        "workspace_name",
        default=None,
        help="The dataset's workspace, when the name is ambiguous.",
    )(command)


def dataset_from_args(ctx: click.Context, client: ArgillaClient, name: str, workspace_name: str | None) -> Resource:
    """Resolve a dataset argument, honoring an explicit --workspace scope."""
    workspace = find_workspace(client, workspace_name) if workspace_name else None
    return find_dataset(client, name, workspace)


def confirm_destructive(ctx: click.Context, command_path: str, yes: bool, details: str = "") -> None:
    """Confirmation with both --yes switches: the group flag and the local one."""
    from argilla_cli.profiles import confirm_destructive as confirm

    global_yes = bool(ctx.obj and ctx.obj.get("yes"))
    confirm(command_path, yes=yes or global_yes, details=details)


def _raw_record_json(client: ArgillaClient, record_id: Any) -> dict[str, Any]:  # noqa: ANN401 -- a server record body
    """The record as the server serialized it, before the SDK model drops fields."""
    api_records = client.api.records
    raw_get = getattr(api_records, "get_json", None)
    if raw_get is not None:
        # Test fakes expose the raw body directly; the real API client only
        # has the HTTP layer.
        body: Any = raw_get(record_id)
        return body if isinstance(body, dict) else {}
    response = api_records.http_client.get(f"/api/v1/records/{record_id}")
    response.raise_for_status()
    body_any: Any = response.json()
    return body_any if isinstance(body_any, dict) else {}


def enforce_record_in_dataset(client: ArgillaClient, dataset: Resource, record_id: object, dataset_name: str) -> None:
    """Refuse to act on a record id that lives in a different dataset.

    The SDK's record model carries no dataset id, so a UUID from another
    dataset would be fetched, updated, or deleted without complaint.
    """
    body = _raw_record_json(client, record_id)
    owner = body.get("dataset_id")
    if owner is not None and str(owner) != str(dataset.id):
        raise ArgillaCLIError(f"Record {record_id} is not in dataset {dataset_name!r}; it belongs to another dataset.")


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
    """Attach the per-command --account and --json flags.

    The group-level --json only parses before the subcommand, which agents
    get wrong; a per-command --json reads naturally in either position. The
    wrapper drops the flag from the callback call; emit() reads it from
    ctx.params, so no command signature has to grow a parameter.
    """
    decorated = click.option("--json", "json_flag", is_flag=True, default=False, help="Output as JSON.")(
        click.option("--account", "account", default=None, help="Use this named account.")(command)
    )

    @functools.wraps(decorated)
    def wrapper(*args: Any, **kwargs: Any) -> Any:  # noqa: ANN401 -- a pass-through boundary
        kwargs.pop("json_flag", None)
        return decorated(*args, **kwargs)

    return cast("FC", wrapper)
