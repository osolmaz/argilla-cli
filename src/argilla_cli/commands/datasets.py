"""datasets: list, get (with a settings dump), create, update, delete, progress."""

from __future__ import annotations

import json
from functools import partial
from typing import TYPE_CHECKING, Any

import click

from argilla_cli.commands.helpers import (
    ArgillaClient,
    confirm_destructive,
    dataset_from_args,
    default_workspace,
    find_dataset,
    find_workspace,
    get_client,
    profile_in_force,
    quiet_call,
    sdk_call,
    with_account,
    with_workspace,
)
from argilla_cli.commands.responses import _status_label
from argilla_cli.errors import ArgillaCLIError
from argilla_cli.output import emit, wants_json
from argilla_cli.profiles import enforce_profile

if TYPE_CHECKING:
    from argilla import Dataset
    from argilla.settings import Field, QuestionType


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
    workspace_id = None
    if workspace_name is not None:
        workspace_id = find_workspace(client, workspace_name).id
    models = client.api.datasets.list(workspace_id=workspace_id)
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


def _load_settings_document(path: str) -> dict[str, Any]:
    """Read a settings document: JSON or YAML, by extension or content."""
    with open(path, encoding="utf-8") as handle:
        content = handle.read()
    document: Any
    if path.endswith(".yaml") or path.endswith(".yml"):
        import yaml

        document = yaml.safe_load(content)
    else:
        try:
            document = json.loads(content)
        except json.JSONDecodeError as exc:
            try:
                import yaml

                document = yaml.safe_load(content)
            except ImportError as import_exc:
                raise ArgillaCLIError(
                    f"Settings file {path} is not JSON and YAML support is unavailable: {exc}"
                ) from import_exc
            if not isinstance(document, dict):
                raise ArgillaCLIError(
                    f"Settings file {path} must contain an object with fields and questions."
                ) from exc
    if not isinstance(document, dict):
        raise ArgillaCLIError(f"Settings file {path} must contain an object with fields and questions.")
    return document


def _settings_from_document(document: dict[str, Any]) -> Any:  # noqa: ANN401 -- SDK Settings type is the boundary
    """Build SDK settings from a document. `_from_dict` is the SDK's own
    loader (its from_json calls it); it accepts the same shape as serialize."""
    import argilla as rg

    return rg.Settings._from_dict(document)


def _settings_for_create(document: dict[str, Any]) -> Any:  # noqa: ANN401 -- SDK Settings type is the boundary
    """Settings for a fresh create: the server wants a bool, not null."""
    settings = _settings_from_document(_clean_settings_document(document))
    if settings.allow_extra_metadata is None:
        settings.allow_extra_metadata = False
    return settings


# Server-assigned keys that must never flow back into a write: an id in a
# field or question makes the SDK PATCH the record the id names, even when
# that dataset is a different one.
_SERVER_ASSIGNED_KEYS = ("id", "dataset_id", "inserted_at", "updated_at")


def _clean_settings_document(document: dict[str, Any]) -> dict[str, Any]:
    """Strip server-assigned keys from a settings document, top level and items."""
    cleaned: dict[str, Any] = {}
    for key, value in document.items():
        if key in _SERVER_ASSIGNED_KEYS:
            continue
        if isinstance(value, list):
            cleaned[key] = [
                {name: item_value for name, item_value in item.items() if name not in _SERVER_ASSIGNED_KEYS}
                if isinstance(item, dict)
                else item
                for item in value
            ]
        else:
            cleaned[key] = value
    return cleaned


def _update_settings_from_document(
    client: ArgillaClient,
    dataset: Any,  # noqa: ANN401 -- SDK Dataset type is the boundary
    document: dict[str, Any],
    guidelines_flag: str | None = None,
) -> None:
    """Apply a settings document to a fetched dataset through the raw APIs.

    The SDK's own Settings.update() rebuilds its resource objects on every
    access, so ids injected into them do not survive to the write; a dump of
    another dataset would CREATE its properties there. Going through the
    group APIs keeps the write on the named dataset: an item the dataset
    already has (by name) is PATCHed by its real id, a new item is created,
    and dataset-level values the document leaves out keep their current
    setting.
    """
    from argilla._models import DatasetModel
    from argilla.settings._field import _field_from_dict
    from argilla.settings._metadata import MetadataField
    from argilla.settings._question import _question_from_dict
    from argilla.settings._task_distribution import TaskDistribution
    from argilla.settings._vector import VectorField

    cleaned = _clean_settings_document(document)
    current = dataset.settings  # fetched by the caller

    for group, api, loader in (
        ("fields", client.api.fields, _field_from_dict),
        ("questions", client.api.questions, _question_from_dict),
        ("vectors", client.api.vectors, VectorField.from_dict),
        ("metadata", client.api.metadata, MetadataField.from_dict),
    ):
        ids_by_name = {item.name: item.id for item in getattr(current, group)}
        for item_doc in cleaned.get(group, []):
            model = loader(item_doc)._model
            model.dataset_id = dataset.id
            current_id = ids_by_name.get(model.name)
            if current_id is not None:
                model.id = current_id
                sdk_call(partial(api.update, model), f"Updating {group}")
            else:
                sdk_call(partial(api.create, model), f"Adding to {group}")

    if "distribution" in cleaned:
        distribution = TaskDistribution.from_dict(cleaned["distribution"])._api_model()
    else:
        distribution = dataset._model.distribution
    patch = DatasetModel(
        id=dataset.id,
        name=dataset.name,
        guidelines=guidelines_flag if guidelines_flag is not None else cleaned.get("guidelines", dataset.guidelines),
        allow_extra_metadata=cleaned.get("allow_extra_metadata", dataset.allow_extra_metadata),
        distribution=distribution,
    )
    sdk_call(lambda: client.api.datasets.update(patch), "Updating dataset")


@datasets.command("create")
@click.argument("name")
@click.option("--workspace", "workspace_name", default=None, help="The workspace to create the dataset in.")
@click.option("--settings", "settings_path", type=click.Path(exists=True, dir_okay=False), default=None)
@click.option("--like", "like_name", default=None, help="Copy the settings of this existing dataset.")
@click.option("--guidelines", default=None, help="Guidelines text shown to annotators.")
@click.option("--dry-run", is_flag=True, default=False, help="Show the plan without creating anything.")
@with_account
@click.pass_context
def datasets_create(
    ctx: click.Context,
    name: str,
    workspace_name: str | None,
    settings_path: str | None,
    like_name: str | None,
    guidelines: str | None,
    dry_run: bool,
    account: str | None,
) -> None:
    """Create the dataset NAME, configured from a settings document."""
    enforce_profile("datasets create", profile_in_force(ctx))
    client = get_client(ctx)
    workspace = find_workspace(client, workspace_name) if workspace_name else default_workspace(client)
    if quiet_call(lambda: client.datasets(name, workspace=workspace)) is not None:
        raise ArgillaCLIError(f"A dataset named {name!r} already exists in workspace {workspace.name!r}.")
    if settings_path and like_name:
        raise ArgillaCLIError("Pass either --settings or --like, not both.")

    settings_doc: dict[str, Any] | None = None
    if settings_path:
        settings_doc = _load_settings_document(settings_path)
    elif like_name:
        source = find_dataset(client, like_name)
        sdk_call(lambda: source.get(), f"Fetching dataset {like_name}")
        settings_doc = source.settings.serialize()

    plan = {
        "action": "create",
        "resource": "dataset",
        "name": name,
        "workspace": workspace.name,
        "settings": settings_doc,
        "guidelines": guidelines,
    }
    if dry_run:
        plan["dry_run"] = True
        emit(ctx, plan)
        return

    import argilla as rg

    dataset = rg.Dataset(name=name, workspace=workspace, client=client)
    if settings_doc is not None:
        # A dump from another dataset must not carry server-assigned ids: a
        # fresh create would build properties pointing at the wrong rows.
        dataset.settings = _settings_for_create(settings_doc)
    if guidelines is not None:
        dataset.settings.guidelines = guidelines
    sdk_call(lambda: dataset.create(), "Creating dataset")
    emit(ctx, {"action": "create", "resource": "dataset", "name": name, "workspace": workspace.name})


@datasets.command("update")
@click.argument("name")
@click.option("--workspace", "workspace_name", default=None, help="The dataset's workspace, when ambiguous.")
@click.option("--settings", "settings_path", type=click.Path(exists=True, dir_okay=False), default=None)
@click.option("--guidelines", default=None, help="New guidelines text.")
@with_account
@click.pass_context
def datasets_update(
    ctx: click.Context,
    name: str,
    workspace_name: str | None,
    settings_path: str | None,
    guidelines: str | None,
    account: str | None,
) -> None:
    """Apply a settings document (and or guidelines) to the dataset NAME."""
    enforce_profile("datasets update", profile_in_force(ctx))
    client = get_client(ctx)
    dataset = dataset_from_args(ctx, client, name, workspace_name)
    sdk_call(lambda: dataset.get(), f"Fetching dataset {name}")
    if not settings_path and guidelines is None:
        raise ArgillaCLIError("Nothing to update: pass --settings or --guidelines.")
    if settings_path:
        # Raw group APIs: properties update in place by name, and values the
        # document omits keep their current setting.
        _update_settings_from_document(client, dataset, _load_settings_document(settings_path), guidelines)
    elif guidelines is not None:
        _update_settings_from_document(client, dataset, {}, guidelines)
    emit(ctx, {"action": "update", "resource": "dataset", "name": name})


@datasets.command("delete")
@click.argument("name")
@click.option("--workspace", "workspace_name", default=None, help="The dataset's workspace, when ambiguous.")
@click.option("--yes", "-y", is_flag=True, default=False, help="Skip the confirmation prompt.")
@with_account
@click.pass_context
def datasets_delete(
    ctx: click.Context,
    name: str,
    workspace_name: str | None,
    yes: bool,
    account: str | None,
) -> None:
    """Delete the dataset NAME and its records."""
    enforce_profile("datasets delete", profile_in_force(ctx))
    confirm_destructive(ctx, "datasets delete", yes=yes, details=name)
    client = get_client(ctx)
    dataset = dataset_from_args(ctx, client, name, workspace_name)
    sdk_call(lambda: dataset.delete(), "Deleting dataset")
    emit(ctx, {"action": "delete", "resource": "dataset", "name": name})


@datasets.command("progress")
@click.argument("name")
@click.option("--workspace", "workspace_name", default=None, help="The dataset's workspace, when ambiguous.")
@click.option("--with-users", is_flag=True, default=False, help="Break progress down per user.")
@with_account
@click.pass_context
def datasets_progress(
    ctx: click.Context,
    name: str,
    workspace_name: str | None,
    with_users: bool,
    account: str | None,
) -> None:
    """Show the annotation progress on the dataset NAME."""
    client = get_client(ctx)
    dataset = dataset_from_args(ctx, client, name, workspace_name)
    progress = sdk_call(
        lambda: dataset.progress(with_users_distribution=with_users),
        "Fetching progress",
    )
    emit(ctx, progress)


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
    dataset = dataset_from_args(ctx, client, name, workspace_name)
    sdk_call(lambda: dataset.get(), f"Fetching dataset {name}")
    resolved_name = _workspace_names(client).get(str(dataset._model.workspace_id))
    if not dump_settings:
        emit(ctx, _dataset_detail(dataset, resolved_name))
        return

    document = _clean_settings_document(dataset.settings.serialize())
    if wants_json(ctx):
        # --json promises JSON on every command; --format yaml is for humans.
        fmt = "json"
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


def _metadata_dict(metadata: Any) -> dict[str, Any]:  # noqa: ANN401 -- server metadata shape varies
    """Metadata as a name/value dict, whatever shape the server returned."""
    if isinstance(metadata, dict):
        return dict(metadata)
    return {item.name: item.value for item in metadata or []}


def _reserved(value: str, name: str) -> None:
    """A field, metadata, or question name that the export format needs."""
    raise ArgillaCLIError(f"A {value} named {name!r} collides with the export's own keys and cannot be exported.")


def _suggestion_flat_values(model: Any, qmap: dict[str, str]) -> dict[str, Any]:  # noqa: ANN401 -- a RecordModel crosses here
    """Suggestion values keyed by question name, reserved names refused."""
    values: dict[str, Any] = {}
    for suggestion in model.suggestions or []:
        name = suggestion.question_name or qmap.get(str(suggestion.question_id))
        if name is not None:
            if name in ("id", "responses"):
                _reserved("question", name)
            values[name] = suggestion.value
    return values


def _export_line(model: Any, qmap: dict[str, str], umap: dict[str, str]) -> dict[str, Any]:  # noqa: ANN401 -- a RecordModel crosses here
    """One JSONL line: the flat record plus its responses."""
    line: dict[str, Any] = {"id": model.external_id if model.external_id is not None else str(model.id)}
    for name, value in {**(model.fields or {}), **_metadata_dict(model.metadata)}.items():
        if name in ("id", "responses"):
            _reserved("field or metadata", name)
        line[name] = value
    line.update(_suggestion_flat_values(model, qmap))
    rows = []
    for response in model.responses or []:
        for qid, payload in (response.values or {}).items():
            rows.append(
                {
                    "question": qmap.get(qid, qid),
                    "value": (payload or {}).get("value"),
                    "user": umap.get(str(response.user_id)) if response.user_id else None,
                    "status": _status_label(response.status),
                }
            )
    if rows:
        line["responses"] = rows
    return line


@datasets.command("export")
@click.argument("name")
@click.option("--workspace", "workspace_name", default=None, help="The dataset's workspace, when ambiguous.")
@click.option(
    "-o", "--output", "output_path", required=True, type=click.Path(dir_okay=False), help="The JSONL file to write."
)
@with_account
@click.pass_context
def datasets_export(
    ctx: click.Context,
    name: str,
    workspace_name: str | None,
    output_path: str,
    account: str | None,
) -> None:
    """Write the dataset NAME's records, suggestions, and responses as JSONL.

    Each line is a flat record (field and metadata names as keys, question
    names holding their suggestion values) plus a 'responses' list. Import
    reads the same format back; responses are kept for review and are not
    re-submitted on import, so no answer is filed twice or under the wrong
    user.
    """
    import os

    client = get_client(ctx)
    dataset = dataset_from_args(ctx, client, name, workspace_name)
    sdk_call(lambda: dataset.get(), f"Fetching dataset {name}")
    qmap = {str(question.id): question.name for question in dataset.settings.questions}
    umap = {str(user.id): user.username for user in client.users}
    if os.path.exists(output_path):
        raise ArgillaCLIError(f"{output_path} already exists; remove it or pick another name.")

    count = 0
    temp_path = output_path + ".partial"
    try:
        with open(temp_path, "w", encoding="utf-8") as handle:
            for record in dataset.records(with_suggestions=True, with_responses=True):
                handle.write(json.dumps(_export_line(record._model, qmap, umap), ensure_ascii=False) + "\n")
                count += 1
        os.replace(temp_path, output_path)
    except BaseException:
        # A failed export must not leave a partial file behind that blocks
        # the next run.
        import contextlib

        with contextlib.suppress(OSError):
            os.unlink(temp_path)
        raise
    emit(ctx, {"action": "export", "resource": "records", "dataset": name, "records": count, "output": output_path})


@datasets.command("import")
@click.argument("name")
@click.argument("file_path", type=click.Path(exists=True, dir_okay=False))
@click.option("--dry-run", is_flag=True, default=False, help="Count the records without sending them.")
@with_workspace
@with_account
@click.pass_context
def datasets_import(
    ctx: click.Context,
    name: str,
    file_path: str,
    dry_run: bool,
    workspace_name: str | None,
    account: str | None,
) -> None:
    """Load records from FILE (as written by export) into the dataset NAME."""
    from argilla_cli.commands.records import _load_records

    enforce_profile("datasets import", profile_in_force(ctx))
    items = _load_records(file_path)
    responses = sum(len(item.get("responses") or []) for item in items)
    records = [{key: value for key, value in item.items() if key != "responses"} for item in items]
    client = get_client(ctx)
    dataset = dataset_from_args(ctx, client, name, workspace_name)
    if dry_run:
        emit(
            ctx,
            {
                "action": "import",
                "dataset": name,
                "records": len(records),
                "responses_kept_for_review": responses,
                "dry_run": True,
            },
        )
        return
    sdk_call(lambda: dataset.get(), f"Fetching dataset {name}")
    sdk_call(lambda: dataset.records.log(records), "Importing records")
    emit(
        ctx,
        {
            "action": "import",
            "dataset": name,
            "records": len(records),
            "responses_kept_for_review": responses,
        },
    )


__all__ = ["_dataset_detail", "_dataset_row", "_export_line", "datasets"]
