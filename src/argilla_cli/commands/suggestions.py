"""suggestions: add.

A suggestion is a proposed answer (typically from a model) attached to a
record's question. The file holds one JSON object per line (or a JSON array):
{"id": "<record id or external id>", "<question name>": value, ...}. Adding a
suggestion never touches the record's fields: the current record is fetched
and re-upserted with the suggestion merged in.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import click

from argilla_cli.commands.helpers import (
    ArgillaClient,
    find_dataset,
    get_client,
    profile_in_force,
    sdk_call,
    with_account,
)
from argilla_cli.errors import ArgillaCLIError
from argilla_cli.output import emit
from argilla_cli.profiles import enforce_profile

if TYPE_CHECKING:
    from uuid import UUID

    from argilla import Dataset, Record
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


def _fetch_record(client: ArgillaClient, record_id: UUID, record_ref: str) -> RecordModel:
    """Fetch the record model, with the ref in the message for humans."""
    model = client.api.records.get(record_id)
    if model is None:
        raise ArgillaCLIError(f"No record {record_ref!r} in this dataset.")
    return model


def _upsert_record(dataset: Dataset, record: Record, record_ref: str) -> None:
    def upsert() -> object:
        return dataset.records.log([record])

    sdk_call(upsert, f"Adding suggestion to record {record_ref}")


@click.group("suggestions")
def suggestions() -> None:
    """Manage suggestions on records."""


@suggestions.command("add")
@click.argument("dataset_name")
@click.option(
    "--file",
    "file_path",
    type=click.Path(exists=True, dir_okay=False),
    required=True,
    help="A .json or .jsonl file, or - for stdin.",
)
@click.option("--dry-run", is_flag=True, default=False, help="Show what would be added without adding it.")
@with_account
@click.pass_context
def suggestions_add(ctx: click.Context, dataset_name: str, file_path: str, dry_run: bool, account: str | None) -> None:
    """Add suggestions to records of DATASET_NAME from FILE."""
    enforce_profile("suggestions add", profile_in_force(ctx))
    docs = _suggestion_docs(file_path)
    client = get_client(ctx)
    dataset = find_dataset(client, dataset_name)
    sdk_call(lambda: dataset.get(), f"Fetching dataset {dataset_name}")
    question_names = {question.name for question in dataset.settings.questions}

    from argilla_cli.commands.responses import _resolve_record_id

    added = 0
    for doc in docs:
        record_ref = doc.pop("id")
        unknown = [name for name in doc if name not in question_names]
        if unknown:
            raise ArgillaCLIError(
                f"Unknown question(s) {', '.join(sorted(unknown))} for record {record_ref!r}. "
                f"Questions in this dataset: {', '.join(sorted(question_names))}."
            )
        if dry_run:
            added += len(doc)
            continue

        import argilla as rg

        record_id = _resolve_record_id(dataset, record_ref)
        model = _fetch_record(client, record_id, record_ref)
        record = rg.Record.from_model(model, dataset=dataset)
        for name, value in doc.items():
            record.suggestions.add(rg.Suggestion(name, value))
        _upsert_record(dataset, record, record_ref)
        added += len(doc)

    emit(
        ctx,
        {"action": "add", "resource": "suggestions", "dataset": dataset_name, "suggestions": added, "dry_run": dry_run},
    )


__all__ = ["_suggestion_docs", "suggestions"]
