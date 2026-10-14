"""The argilla-cli entry point.

A single click group. Global flags carry credentials (--url, --username,
--password, --api-key, --credentials-file), account selection (--account),
output (--json), and safety (--yes, --profile) for every command. Errors print
one redacted line, never a traceback, and exit non-zero.
"""

import sys

import click

from argilla_cli import __version__, masking
from argilla_cli.commands.account import account_group, config_group, login_cmd
from argilla_cli.commands.core import doctor_cmd, info_cmd, setup_cmd, whoami_cmd


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
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError, AttributeError):
            # A wrapped or detached stream (pytest, some CI harnesses) cannot
            # be reconfigured; leaving it alone is better than failing.
            pass


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
@click.option("--yes", "-y", is_flag=True, default=False,
              help="Skip confirmation prompts for destructive actions.")
@click.option("--profile", "profile", default=None,
              type=click.Choice(["full", "read-only"]),
              help="Override the account's permission profile for this invocation.")
@click.option("--account", "account", default=None,
              help="Use this named account instead of the active one.")
@click.option("--url", "url", default=None, help="Argilla server URL.")
@click.option("--username", "username", default=None, help="Account username.")
@click.option("--password", "password", default=None, help="Account password.")
@click.option("--api-key", "api_key", default=None, help="API key.")
@click.option("--credentials-file", "credentials_file", default=None,
              help="Path to an env file with credentials.")
@click.pass_context
def main(ctx, use_json, yes, profile, account, url, username, password, api_key, credentials_file):
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
    except Exception as exc:  # noqa: BLE001 - no traceback escapes to the user
        click.echo(f"Error: {masking.redact_message(str(exc))}", err=True)
        sys.exit(1)
