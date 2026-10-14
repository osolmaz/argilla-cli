"""Small shared helpers for test modules."""

from typing import Any


def invoke(_client: Any, args: list[str], **kwargs: Any) -> Any:
    """Run the CLI with a fresh context obj; pass the fake client first."""
    from click.testing import CliRunner

    from argilla_cli.cli import main

    return CliRunner().invoke(main, args, obj={}, **kwargs)
