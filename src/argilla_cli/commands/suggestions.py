"""suggestions: add.

A suggestion is a proposed answer (typically from a model) attached to a
record's question. The file holds one JSON object per line (or a JSON array):
{"id": "<record id or external id>", "<question name>": value, ...}. Adding a
suggestion never touches the record's fields or responses: every document is
validated first, record references resolve in one pass, and only the
suggestion is written.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import click

from argilla_cli.commands.helpers import (
    dataset_from_args,
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
    from argilla import Dataset
    from argilla._models import RecordModel


def _suggestion_docs(file_path: str) -> list[dict[str, Any]]:
    from argilla_cli.commands.records import _load_records

    items = _load_records(file_path)
    docs: list[dict[str, Any]] = []
    for item in items:
        record_ref = item.get("id") or item.get("record_id")
        if not record_ref:
            raise ArgillaCLIError("Every suggestion entry needs an 'id' pointing at its record.")
        suggestions = item.get("suggestions")
        if isinstance(suggestions, dict):
            docs.append({"id": str(record_ref), **dict(suggestions.items())})
            continue
        pairs = {name: value for name, value in item.items() if name not in ("id", "record_id")}
        if not pairs:
            raise ArgillaCLIError(f"Record {record_ref!r} has no suggestions to add.")
        docs.append({"id": str(record_ref), **pairs})
    return docs


def _validate_suggestion_docs(docs: list[dict[str, Any]], question_names: set[str]) -> None:
    """Validate every document before writing anything: a bad line must not
    leave the lines before it applied."""
    for doc in docs:
        unknown = [name for name in doc if name != "id" and name not in question_names]
        if unknown:
            raise ArgillaCLIError(
                f"Unknown question(s) {', '.join(sorted(unknown))} for record {doc.get('id')!r}. "
                f"Questions in this dataset: {', '.join(sorted(question_names))}."
            )


def _record_model_index(dataset: Dataset) -> dict[str, RecordModel]:
    """One pass over the dataset maps every record reference to its model;
    a per-reference scan would be records times records."""
    models: dict[str, RecordModel] = {}
    for record in dataset.records(with_suggestions=False, with_responses=False):
        model = record._model
        if model.external_id is not None:
            models[str(model.external_id)] = model
        if model.id is not None:
            models[str(model.id)] = model
    return models


def _add_suggestion(dataset: Dataset, model: RecordModel, doc: dict[str, Any]) -> int:
    """Attach one document's suggestions to its record and upsert."""
    import argilla as rg

    model.responses = []  # never resend the responses that are already there
    record = rg.Record.from_model(model, dataset=dataset)
    for name, value in doc.items():
        record.suggestions.add(rg.Suggestion(name, value))
    sdk_call(lambda: dataset.records.log([record]), "Adding suggestion")
    return len(doc)


@click.group("suggestions")
def suggestions() -> None:
    """Manage suggestions on records."""


@suggestions.command("add")
@click.argument("dataset_name")
@click.option(
    "--file",
    "file_path",
    type=click.Path(exists=True, dir_okay=False, allow_dash=True),
    required=True,
    help="A .json or .jsonl file, or - for stdin.",
)
@click.option("--dry-run", is_flag=True, default=False, help="Show what would be added without adding it.")
@with_workspace
@with_account
@click.pass_context
def suggestions_add(
    ctx: click.Context,
    dataset_name: str,
    file_path: str,
    dry_run: bool,
    workspace_name: str | None,
    account: str | None,
) -> None:
    """Add suggestions to records of DATASET_NAME from FILE."""
    enforce_profile("suggestions add", profile_in_force(ctx))
    docs = _suggestion_docs(file_path)
    client = get_client(ctx)
    dataset = dataset_from_args(ctx, client, dataset_name, workspace_name)
    sdk_call(lambda: dataset.get(), f"Fetching dataset {dataset_name}")
    question_names = {question.name for question in dataset.settings.questions}
    _validate_suggestion_docs(docs, question_names)

    if dry_run:
        emit(
            ctx,
            {
                "action": "add",
                "resource": "suggestions",
                "dataset": dataset_name,
                "suggestions": sum(len(doc) for doc in docs),
                "records": len(docs),
                "dry_run": True,
            },
        )
        return

    models = _record_model_index(dataset)
    added = 0
    for doc in docs:
        record_ref = doc.pop("id")
        model = models.get(str(record_ref))
        if model is None:
            raise ArgillaCLIError(f"No record {record_ref!r} in dataset {dataset.name!r}.")
        added += _add_suggestion(dataset, model, doc)

    emit(ctx, {"action": "add", "resource": "suggestions", "dataset": dataset_name, "suggestions": added})


__all__ = ["_suggestion_docs", "suggestions"]
