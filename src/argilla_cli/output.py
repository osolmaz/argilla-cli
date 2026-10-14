"""Output helpers: --json on every command, short tables otherwise.

`emit()` is the only place a command prints its result. With --json the data
goes out as one JSON document; without it the data becomes a small table (or
key/value lines for a single object). Secrets never enter `data` in the first
place, so nothing here needs to redact.
"""

import json
from typing import Any

import click


def emit(
    ctx: click.Context | None,
    data: Any,  # noqa: ANN401 -- every command result shape crosses this one boundary
    columns: list[tuple[str, str]] | None = None,
    table: list[dict[str, Any]] | None = None,
) -> None:
    """Print a command result.

    data:   what --json prints: a dict, or a list of dicts.
    table:  optional simpler rows for table mode (list of dicts of scalars).
    columns: (key, header) pairs for table mode; defaults to the data's keys.
    """
    use_json = bool(ctx.obj.get("use_json", False)) if ctx else False
    if use_json:
        click.echo(json.dumps(data, indent=2, ensure_ascii=False, default=str))
        return

    if isinstance(data, dict):
        _print_key_value(data)
        return

    rows = table if table is not None else data
    if not rows:
        click.echo("No results.")
        return
    _print_table(rows, columns)


def _cell(value: Any) -> str:  # noqa: ANN401 -- renders whatever a command put in a row
    if value is None:
        return ""
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _print_table(rows: list[dict[str, Any]], columns: list[tuple[str, str]] | None = None) -> None:
    if columns is None:
        keys = list(rows[0].keys())
        columns = [(key, key) for key in keys]
    headers = [header for _, header in columns]
    rendered = [[_cell(row.get(key, "")) for key, _ in columns] for row in rows]
    widths = [
        max(len(headers[i]), *(len(row[i]) for row in rendered)) if rendered else len(headers[i])
        for i in range(len(columns))
    ]

    def line(cells: list[str]) -> str:
        return "  ".join(cell.ljust(width) for cell, width in zip(cells, widths, strict=True)).rstrip()

    click.echo(line(headers))
    click.echo(line(["-" * width for width in widths]))
    for row in rendered:
        click.echo(line(row))


def _print_key_value(data: dict[str, Any]) -> None:
    width = max((len(key) for key in data), default=0)
    for key, value in data.items():
        click.echo(f"{key.ljust(width)}  {_cell(value)}")
