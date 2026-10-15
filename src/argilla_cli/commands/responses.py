"""responses: list, export, submit.

A response is one user's answer to a record's question. Submitting takes
question names (as in the dataset schema) and resolves them to ids. Listing
and export filter by username and status.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import click

from argilla_cli.commands.helpers import (
    ArgillaClient,
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
    from argilla import Dataset
    from argilla._models import RecordModel

STATUS_CHOICES = ("draft", "submitted", "discarded")


def _status_label(status: Any) -> str | None:  # noqa: ANN401 -- enum or plain string from the SDK
    """Render the SDK's ResponseStatus enum as its plain name."""
    if status is None:
        return None
    return str(getattr(status, "value", status))


def _question_names(dataset: Dataset) -> dict[str, str]:
    """Map the str(question id) to the question name, using the fetched schema."""
    return {str(question.id): question.name for question in dataset.settings.questions}


def _usernames(client: Any) -> dict[str, str]:  # noqa: ANN401 -- SDK client surface is wide
    return {str(user.id): user.username for user in client.users}


def _iter_response_rows(
    dataset: Dataset,
    client: ArgillaClient,
    user: str | None,
    status: str | None,
) -> list[dict[str, Any]]:
    qmap = _question_names(dataset)
    umap = _usernames(client)
    rows: list[dict[str, Any]] = []
    for record in dataset.records(with_suggestions=True, with_responses=True):
        model: RecordModel = record._model
        for response in model.responses or []:
            row_user = umap.get(str(response.user_id)) if response.user_id else None
            if user is not None and row_user != user:
                continue
            if status is not None and _status_label(response.status) != status:
                continue
            for qid, payload in (response.values or {}).items():
                rows.append(
                    {
                        "record": model.external_id or str(model.id),
                        "question": qmap.get(qid, qid),
                        "value": (payload or {}).get("value"),
                        "user": row_user,
                        "status": _status_label(response.status),
                    }
                )
    return rows


def _resolve_record_id(dataset: Dataset, record_ref: str) -> Any:  # noqa: ANN401 -- a server UUID crosses here
    """Accept a server record id (UUID) or an external id.

    A bare UUID string is ambiguous: the SDK gives records without an
    explicit id a random-UUID external id, so our own exports carry
    UUID-shaped external ids. The external id wins over the server id.
    """
    for record in dataset.records(with_suggestions=False, with_responses=False):
        model = record._model
        if model.external_id == record_ref or str(model.id) == record_ref:
            return model.id
    raise ArgillaCLIError(f"No record {record_ref!r} in dataset {dataset.name!r}.")


@click.group("responses")
def responses() -> None:
    """Manage responses to records."""


@responses.command("list")
@click.argument("dataset_name")
@click.option("--user", "user_name", default=None, help="Only responses by this username.")
@click.option("--status", type=click.Choice(STATUS_CHOICES), default=None, help="Only this status.")
@with_workspace
@with_account
@click.pass_context
def responses_list(
    ctx: click.Context,
    dataset_name: str,
    user_name: str | None,
    status: str | None,
    workspace_name: str | None,
    account: str | None,
) -> None:
    """List the responses on the dataset DATASET_NAME."""
    client = get_client(ctx)
    dataset = dataset_from_args(ctx, client, dataset_name, workspace_name)
    sdk_call(lambda: dataset.get(), f"Fetching dataset {dataset_name}")
    emit(ctx, _iter_response_rows(dataset, client, user_name, status))


@responses.command("export")
@click.argument("dataset_name")
@click.option("-o", "--output", "output_path", required=True, type=click.Path(dir_okay=False, allow_dash=True))
@click.option("--user", "user_name", default=None, help="Only responses by this username.")
@click.option("--status", type=click.Choice(STATUS_CHOICES), default=None, help="Only this status.")
@with_workspace
@with_account
@click.pass_context
def responses_export(
    ctx: click.Context,
    dataset_name: str,
    output_path: str,
    user_name: str | None,
    status: str | None,
    workspace_name: str | None,
    account: str | None,
) -> None:
    """Write the dataset's responses as a JSONL file."""
    client = get_client(ctx)
    dataset = dataset_from_args(ctx, client, dataset_name, workspace_name)
    sdk_call(lambda: dataset.get(), f"Fetching dataset {dataset_name}")
    rows = _iter_response_rows(dataset, client, user_name, status)
    if output_path == "-":
        for row in rows:
            click.echo(json.dumps(row, ensure_ascii=False))
        return
    import os

    if os.path.exists(output_path):
        raise ArgillaCLIError(f"{output_path} already exists; remove it or pick another name.")
    temp_path = output_path + ".partial"
    try:
        with open(temp_path, "w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        os.replace(temp_path, output_path)
    except BaseException:
        import contextlib

        with contextlib.suppress(OSError):
            os.unlink(temp_path)
        raise
    emit(
        ctx,
        {
            "action": "export",
            "resource": "responses",
            "dataset": dataset_name,
            "rows": len(rows),
            "output": output_path,
        },
    )


@responses.command("submit")
@click.argument("dataset_name")
@click.argument("record_ref")
@click.option("--answers", required=True, help='JSON object mapping question name to value, as in {"quality": "ok"}.')
@click.option("--status", type=click.Choice(STATUS_CHOICES), default="submitted", show_default=True)
@click.option("--dry-run", is_flag=True, default=False, help="Show the response without submitting it.")
@with_workspace
@with_account
@click.pass_context
def responses_submit(
    ctx: click.Context,
    dataset_name: str,
    record_ref: str,
    answers: str,
    status: str,
    dry_run: bool,
    workspace_name: str | None,
    account: str | None,
) -> None:
    """Submit a response to one record of DATASET_NAME, filed under your account.

    The 2.8 server pins every response to the authenticated user, so there
    is no --user here: an owner cannot file for someone else.
    """
    enforce_profile("responses submit", profile_in_force(ctx))
    client = get_client(ctx)
    dataset = dataset_from_args(ctx, client, dataset_name, workspace_name)
    sdk_call(lambda: dataset.get(), f"Fetching dataset {dataset_name}")
    qmap = {question.name: question for question in dataset.settings.questions}
    answers_doc = _parse_answers(answers, qmap)

    record_id = _resolve_record_id(dataset, record_ref)
    enforce_record_in_dataset(client, dataset, record_id, dataset_name)

    # The model lives one level down in the SDK; the resource wrappers do
    # not re-export it. The server keys response values by question name.
    from argilla._models import UserResponseModel

    model = UserResponseModel(
        values={name: {"value": value} for name, value in answers_doc.items()},
        status=status,
    )
    if dry_run:
        emit(
            ctx,
            {
                "action": "submit",
                "dataset": dataset_name,
                "record": record_ref,
                "answers": answers_doc,
                "status": status,
                "dry_run": True,
            },
        )
        return
    sdk_call(lambda: client.api.records.create_record_response(record_id, model), "Submitting response")
    emit(
        ctx,
        {
            "action": "submit",
            "dataset": dataset_name,
            "record": record_ref,
            "answers": answers_doc,
            "status": status,
        },
    )


def _parse_answers(text: str, qmap: dict[str, Any]) -> dict[str, Any]:
    try:
        parsed: Any = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ArgillaCLIError(f"--answers is not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict) or not parsed:
        raise ArgillaCLIError("--answers must be a JSON object with at least one question.")
    unknown = [name for name in parsed if name not in qmap]
    if unknown:
        raise ArgillaCLIError(
            f"Unknown question(s) {', '.join(sorted(unknown))}. Questions in this dataset: {', '.join(sorted(qmap))}."
        )
    return parsed


__all__ = ["_iter_response_rows", "_resolve_record_id", "responses"]
