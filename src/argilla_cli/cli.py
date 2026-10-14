"""The argilla-cli entry point.

A single click group. Global flags carry credentials (--url, --username,
--password, --api-key, --credentials-file), account selection (--account),
output (--json), and safety (--yes, --profile) for every command. Errors print
one redacted line, never a traceback, and exit non-zero.
"""

import contextlib
import sys

import click

from argilla_cli import __version__, masking
from argilla_cli.commands.account import account_group, config_group, login_cmd
from argilla_cli.commands.core import doctor_cmd, info_cmd, setup_cmd, whoami_cmd
from argilla_cli.commands.datasets import datasets as datasets_group
from argilla_cli.commands.records import records as records_group
from argilla_cli.commands.responses import responses as responses_group
from argilla_cli.commands.suggestions import suggestions as suggestions_group
from argilla_cli.commands.users import users as users_group
from argilla_cli.commands.workspaces import workspaces as workspaces_group


def _force_utf8_output() -> None:
    """Make stdout/stderr able to carry any dataset content.

    Piped output on some platforms defaults to a legacy encoding, and dataset
    text is arbitrary UTF-8. errors="replace" means a console that cannot
    render a glyph shows "?" rather than taking the command down with it.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        # A wrapped or detached stream (pytest, some CI harnesses) cannot be
        # reconfigured; leaving it alone is better than failing to start.
        with contextlib.suppress(ValueError, OSError, AttributeError):
            reconfigure(encoding="utf-8", errors="replace")


@click.group()
@click.version_option(
    __version__,
    "-V",
    "--version",
    package_name="argilla-cli",
    prog_name="argilla-cli",
    message="%(prog)s version %(version)s",
)
@click.option("--json", "use_json", is_flag=True, default=False, help="Output as JSON.")
@click.option("--yes", "-y", is_flag=True, default=False, help="Skip confirmation prompts for destructive actions.")
@click.option(
    "--profile",
    "profile",
    default=None,
    type=click.Choice(["full", "read-only"]),
    help="Override the account's permission profile for this invocation.",
)
@click.option("--account", "account", default=None, help="Use this named account instead of the active one.")
@click.option("--url", "url", default=None, help="Argilla server URL.")
@click.option("--username", "username", default=None, help="Account username.")
@click.option("--password", "password", default=None, help="Account password.")
@click.option("--api-key", "api_key", default=None, help="API key.")
@click.option("--credentials-file", "credentials_file", default=None, help="Path to an env file with credentials.")
@click.pass_context
def main(
    ctx: click.Context,
    use_json: bool,
    yes: bool,
    profile: str | None,
    account: str | None,
    url: str | None,
    username: str | None,
    password: str | None,
    api_key: str | None,
    credentials_file: str | None,
) -> None:
    """argilla-cli -- Argilla 2.x from the terminal, for humans and agents."""
    _force_utf8_output()
    ctx.ensure_object(dict)
    ctx.obj["use_json"] = use_json
    ctx.obj["yes"] = yes
    ctx.obj["profile"] = profile
    ctx.obj["account"] = account
    ctx.obj["url"] = url
    ctx.obj["username"] = username
    ctx.obj["password"] = password
    ctx.obj["api_key"] = api_key
    ctx.obj["credentials_file"] = credentials_file


main.add_command(login_cmd)
main.add_command(account_group)
main.add_command(config_group)
main.add_command(whoami_cmd)
main.add_command(info_cmd)
main.add_command(doctor_cmd)
main.add_command(setup_cmd)
main.add_command(workspaces_group)
main.add_command(users_group)
main.add_command(datasets_group)
main.add_command(records_group)
main.add_command(responses_group)
main.add_command(suggestions_group)


def main_entry() -> None:
    """Console script entry: run the CLI, print one redacted error line on failure."""
    try:
        main(standalone_mode=False)
    except click.ClickException as exc:
        click.echo(f"Error: {masking.redact_message(exc.format_message())}", err=True)
        sys.exit(exc.exit_code)
    except click.Abort:
        click.echo("Aborted.", err=True)
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as exc:  # a traceback never escapes to the user
        click.echo(f"Error: {masking.redact_message(str(exc))}", err=True)
        sys.exit(1)
