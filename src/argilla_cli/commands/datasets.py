"""datasets: list and get (with a settings dump).

Milestone 3 adds create, update, delete, and progress here.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import click

if TYPE_CHECKING:
    from argilla import Dataset
    from argilla.settings import Field, QuestionType

from argilla_cli.commands.helpers import (
    ArgillaClient,
    find_dataset,
    find_workspace,
    get_client,
    sdk_call,
    with_account,
)
from argilla_cli.output import emit


def _summary(field: Field | QuestionType) -> dict[str, Any]:
    return {
        "name": field.name,
        "title": field.title,
        "required": bool(field.required),
        "type": field.type,
    }


def _dataset_row(dataset: Dataset, workspace_name: str | None) -> dict[str, Any]:
    model = dataset._model
    return {
        "name": dataset.name,
        "id": str(dataset.id) if dataset.id else None,
        "workspace": workspace_name,
        "status": model.status,
        "last_activity_at": str(model.last_activity_at) if model.last_activity_at else None,
    }


def _dataset_detail(dataset: Dataset, workspace_name: str | None) -> dict[str, Any]:
    row = _dataset_row(dataset, workspace_name)
    row.update(
        {
            "guidelines": dataset.guidelines,
            "allow_extra_metadata": dataset.allow_extra_metadata,
            "fields": [_summary(field) for field in dataset.settings.fields],
            "questions": [_summary(question) for question in dataset.settings.questions],
            "metadata": [field.name for field in dataset.settings.metadata],
            "vectors": [field.name for field in dataset.settings.vectors],
        }
    )
    return row


def _workspace_names(client: ArgillaClient) -> dict[str, str]:
    return {str(ws.id): ws.name for ws in client.workspaces}


@click.group("datasets")
def datasets() -> None:
    """Manage datasets."""


@datasets.command("list")
@click.option("--workspace", "workspace_name", default=None, help="Only datasets in this workspace.")
@with_account
@click.pass_context
def datasets_list(ctx: click.Context, workspace_name: str | None, account: str | None) -> None:
    """List datasets, newest first."""
    client = get_client(ctx)
    names = _workspace_names(client)
    models = client.api.datasets.list(workspace_name=workspace_name)
    rows = []
    for model in models:
        rows.append(
            {
                "name": model.name,
                "id": str(model.id) if model.id else None,
                "workspace": names.get(str(model.workspace_id)),
                "status": model.status,
                "last_activity_at": str(model.last_activity_at) if model.last_activity_at else None,
            }
        )
    rows.sort(key=lambda row: row["last_activity_at"] or "", reverse=True)
    emit(ctx, rows)


@datasets.command("get")
@click.argument("name")
@click.option("--workspace", "workspace_name", default=None, help="The dataset's workspace, when ambiguous.")
@click.option("--settings", "dump_settings", is_flag=True, default=False, help="Dump the full settings document.")
@click.option("--format", "fmt", type=click.Choice(("json", "yaml")), default="json", show_default=True)
@click.option("-o", "--output", "output_path", type=click.Path(dir_okay=False), default=None)
@with_account
@click.pass_context
def datasets_get(
    ctx: click.Context,
    name: str,
    workspace_name: str | None,
    dump_settings: bool,
    fmt: str,
    output_path: str | None,
    account: str | None,
) -> None:
    """Show the dataset NAME, optionally its full settings document."""
    client = get_client(ctx)
    workspace = find_workspace(client, workspace_name) if workspace_name else None
    dataset = find_dataset(client, name, workspace)
    sdk_call(lambda: dataset.get(), f"Fetching dataset {name}")
    resolved_name = workspace.name if workspace else _workspace_names(client).get(str(dataset._model.workspace_id))
    if not dump_settings:
        emit(ctx, _dataset_detail(dataset, resolved_name))
        return

    document = dataset.settings.serialize()
    if fmt == "yaml":
        import yaml

        rendered = yaml.safe_dump(document, sort_keys=False, allow_unicode=True)
    else:
        rendered = json.dumps(document, indent=2, ensure_ascii=False)

    if output_path:
        with open(output_path, "w", encoding="utf-8") as handle:
            handle.write(rendered)
        emit(ctx, {"action": "dump-settings", "dataset": name, "output": output_path, "format": fmt})
    else:
        click.echo(rendered, nl=False)


__all__ = ["_dataset_detail", "_dataset_row", "datasets"]
