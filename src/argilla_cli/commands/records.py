"""records: add, list, get, update, delete.

Records move as JSON or JSONL documents. An ``id`` key in a record makes the
add an upsert on that server id, matching the SDK's bulk upsert.
"""

from __future__ import annotations

import json
import sys
from typing import TYPE_CHECKING, Any, cast

import click

from argilla_cli.commands.helpers import (
    confirm_destructive,
    dataset_from_args,
    enforce_record_in_dataset,
    get_client,
    profile_in_force,
    sdk_call,
    with_account,
    with_workspace,
)
from argilla_cli.errors import ArgillaCLIError
from argilla_cli.output import emit
from argilla_cli.profiles import enforce_profile

if TYPE_CHECKING:
    from argilla._models import RecordModel


def _load_records(file_path: str) -> list[dict[str, Any]]:
    """Read records from a .json or .jsonl file, or stdin with -.

    The format is detected by content: text that parses as one JSON document
    is one; otherwise each non-empty line must be a JSON object (JSONL).
    """
    if file_path == "-":
        text = sys.stdin.read()
        label = "<stdin>"
    else:
        with open(file_path, encoding="utf-8") as handle:
            text = handle.read()
        label = file_path

    items: list[Any]
    try:
        parsed = json.loads(text)
        items = [parsed] if isinstance(parsed, dict) else parsed
    except json.JSONDecodeError:
        items = []
        for line_number, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                items.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ArgillaCLIError(f"{label}: line {line_number} is not valid JSON: {exc}") from exc
    if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
        raise ArgillaCLIError("Records must be a JSON array of objects (or JSONL, one object per line).")
    if not items:
        raise ArgillaCLIError("No records found in the input.")
    return items


def _record_row(model: RecordModel) -> dict[str, Any]:
    return {
        "id": str(model.id) if model.id else None,
        "external_id": model.external_id,
        "status": model.status,
        "inserted_at": str(model.inserted_at) if model.inserted_at else None,
        "updated_at": str(model.updated_at) if model.updated_at else None,
    }


def _record_detail(model: RecordModel) -> dict[str, Any]:
    """The full server record, question names still server-shaped."""
    return cast(dict[str, Any], model.model_dump(mode="json"))


def _parse_json_object(text: str, what: str) -> dict[str, Any]:
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ArgillaCLIError(f"{what} is not valid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ArgillaCLIError(f"{what} must be a JSON object.")
    return value


@click.group("records")
def records() -> None:
    """Manage records in a dataset."""


@records.command("add")
@click.argument("dataset_name")
@click.option(
    "--file",
    "file_path",
    type=click.Path(dir_okay=False, allow_dash=True),
    default=None,
    help="A .json or .jsonl file, or - for stdin.",
)
@click.option("--dry-run", is_flag=True, default=False, help="Count the records without sending them.")
@with_workspace
@with_account
@click.pass_context
def records_add(
    ctx: click.Context,
    dataset_name: str,
    file_path: str | None,
    dry_run: bool,
    workspace_name: str | None,
    account: str | None,
) -> None:
    """Add (or upsert by id) the records from FILE into DATASET_NAME."""
    enforce_profile("records add", profile_in_force(ctx))
    if file_path is None:
        raise ArgillaCLIError("Pass --file PATH with the records (use - to read stdin).")
    items = _load_records(file_path)
    client = get_client(ctx)
    dataset = dataset_from_args(ctx, client, dataset_name, workspace_name)
    if dry_run:
        emit(ctx, {"action": "add", "dataset": dataset_name, "records": len(items), "dry_run": True})
        return
    # The ingest maps record keys against the dataset schema, so fetch it first.
    sdk_call(lambda: dataset.get(), f"Fetching dataset {dataset_name}")
    sdk_call(lambda: dataset.records.log(items), "Adding records")
    emit(ctx, {"action": "add", "dataset": dataset_name, "records": len(items)})


@records.command("list")
@click.argument("dataset_name")
@click.option("--limit", type=int, default=20, show_default=True, help="How many records to show.")
@click.option("--offset", type=int, default=0, show_default=True, help="Where to start in the record list.")
@with_workspace
@with_account
@click.pass_context
def records_list(
    ctx: click.Context,
    dataset_name: str,
    limit: int,
    offset: int,
    workspace_name: str | None,
    account: str | None,
) -> None:
    """List the records of DATASET_NAME, oldest first."""
    client = get_client(ctx)
    dataset = dataset_from_args(ctx, client, dataset_name, workspace_name)
    rows: list[dict[str, Any]] = []
    for record in dataset.records(start_offset=offset, limit=limit):
        model = record._model
        if ctx.obj and ctx.obj.get("use_json"):
            rows.append(_record_detail(model))
        else:
            rows.append(_record_row(model))
    emit(ctx, rows)


@records.command("get")
@click.argument("dataset_name")
@click.argument("record_id")
@with_workspace
@with_account
@click.pass_context
def records_get(
    ctx: click.Context, dataset_name: str, record_id: str, workspace_name: str | None, account: str | None
) -> None:
    """Show one record of DATASET_NAME by its server id."""
    from uuid import UUID

    client = get_client(ctx)
    dataset = dataset_from_args(ctx, client, dataset_name, workspace_name)
    try:
        record_uuid = UUID(record_id)
    except ValueError as exc:
        raise ArgillaCLIError(f"{record_id!r} is not a record id (expected a UUID).") from exc
    enforce_record_in_dataset(client, dataset, record_uuid, dataset_name)
    model = sdk_call(lambda: client.api.records.get(record_uuid), "Fetching record")
    emit(ctx, _record_detail(model))


@records.command("update")
@click.argument("dataset_name")
@click.argument("record_id")
@click.option("--fields", default=None, help="JSON object replacing the record's fields.")
@click.option("--metadata", default=None, help="JSON object replacing the record's metadata.")
@with_workspace
@with_account
@click.pass_context
def records_update(
    ctx: click.Context,
    dataset_name: str,
    record_id: str,
    fields: str | None,
    metadata: str | None,
    workspace_name: str | None,
    account: str | None,
) -> None:
    """Replace the fields or metadata of one record."""
    enforce_profile("records update", profile_in_force(ctx))
    if fields is None and metadata is None:
        raise ArgillaCLIError("Nothing to update: pass --fields or --metadata.")
    from uuid import UUID

    client = get_client(ctx)
    dataset = dataset_from_args(ctx, client, dataset_name, workspace_name)
    try:
        record_uuid = UUID(record_id)
    except ValueError as exc:
        raise ArgillaCLIError(f"{record_id!r} is not a record id (expected a UUID).") from exc
    enforce_record_in_dataset(client, dataset, record_uuid, dataset_name)
    model = sdk_call(lambda: client.api.records.get(record_uuid), "Fetching record")
    if fields is not None:
        model.fields = _parse_json_object(fields, "--fields")
    if metadata is not None:
        model.metadata = _parse_json_object(metadata, "--metadata")
    updated = sdk_call(lambda: client.api.records.update(model), "Updating record")
    emit(ctx, _record_detail(updated))


@records.command("delete")
@click.argument("dataset_name")
@click.argument("record_id")
@click.option("--yes", "-y", is_flag=True, default=False, help="Skip the confirmation prompt.")
@with_workspace
@with_account
@click.pass_context
def records_delete(
    ctx: click.Context,
    dataset_name: str,
    record_id: str,
    yes: bool,
    workspace_name: str | None,
    account: str | None,
) -> None:
    """Delete one record of DATASET_NAME by its server id."""
    enforce_profile("records delete", profile_in_force(ctx))
    from uuid import UUID

    client = get_client(ctx)
    dataset = dataset_from_args(ctx, client, dataset_name, workspace_name)
    try:
        record_uuid = UUID(record_id)
    except ValueError as exc:
        raise ArgillaCLIError(f"{record_id!r} is not a record id (expected a UUID).") from exc
    enforce_record_in_dataset(client, dataset, record_uuid, dataset_name)
    confirm_destructive(ctx, "records delete", yes=yes, details=f"{dataset_name} record {record_id}")
    sdk_call(lambda: client.api.records.delete(record_uuid), "Deleting record")
    emit(ctx, {"action": "delete", "resource": "record", "id": record_id})


__all__ = ["_load_records", "_record_detail", "_record_row", "records"]
