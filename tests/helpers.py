"""Small shared helpers for test modules."""

from pathlib import Path
from typing import Any


def invoke(_client: Any, args: list[str], **kwargs: Any) -> Any:
    """Run the CLI with a fresh context obj; pass the fake client first."""
    from click.testing import CliRunner

    from argilla_cli.cli import main

    return CliRunner().invoke(main, args, obj={}, **kwargs)


def set_profile(config_path: Path, profile: str) -> None:
    """Set the profile on the fake account created by the fake_ctx fixture."""
    from argilla_cli import config as config_mod

    config = config_mod.load_config(config_path)
    config["accounts"]["fake"]["profile"] = profile
    config_mod.save_config(config, config_path)
    config = config_mod.load_config(config_path)
    config["active_account"] = "fake"
    config_mod.save_config(config, config_path)
