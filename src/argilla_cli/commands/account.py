"""Login and named-account management, plus `config show`."""

import sys
from urllib.parse import urlparse

import click

from argilla_cli import auth, config as config_mod, masking
from argilla_cli.errors import ArgillaCLIError, CredentialsError
from argilla_cli.output import emit


def has_terminal_stdin() -> bool:
    return sys.stdin is not None and sys.stdin.isatty()


def _account_summary(name: str, entry: dict, active: str | None) -> dict:
    summary: dict = {"name": name, "active": name == active}
    summary.update(masking.mask_mapping(entry))
    return summary


@click.command("login")
@click.option("--url", "url", default=None, help="Argilla server URL, as in https://studio.example.org.")
@click.option("--username", "username", default=None, help="Account username.")
@click.option("--password", "password", default=None,
              help="Account password. A hidden prompt asks when this is missing and there is a terminal.")
@click.option("--api-key", "api_key", default=None, help="API key, for accounts that use one instead of a password.")
@click.option("--credentials-file", "credentials_file", default=None,
              help="Path to an env file with USERNAME and PASSWORD (or ARGILLA_* names). The file stays where it is.")
@click.option("--account", "account", default=None, help="Name to save this login under.")
@click.option("--profile", "profile", default=None, type=click.Choice(config_mod.VALID_PROFILES),
              help="Permission profile for the saved account.")
@click.pass_context
def login_cmd(ctx, url, username, password, api_key, credentials_file, account, profile):
    """Log in to an Argilla server and save the account.

    Checks the credentials against the server and stores the account in the
    config. Secrets the user did not pass on the command line stay out of the
    config: point --credentials-file at an env file and the CLI reads it at
    run time.
    """
    flags = {"url": url, "username": username, "password": password,
             "api_key": api_key, "credentials_file": credentials_file}
    creds = auth.resolve_credentials(ctx, flags=flags)

    if not creds.url:
        raise CredentialsError("No server URL. Pass --url or set ARGILLA_API_URL.")
    if not creds.username:
        raise CredentialsError("No username. Pass --username or set ARGILLA_USERNAME.")

    if not creds.api_key and not creds.password:
        if has_terminal_stdin():
            creds.password = click.prompt(
                f"Password for {creds.username} at {creds.url}", hide_input=True
            )
            creds.password_explicit = True
            masking.register_secrets([creds.password])
        else:
            raise CredentialsError(
                "No password or API key. Pass --password, --api-key, or --credentials-file."
            )

    if creds.api_key:
        user = auth.login_with_api_key(creds.url, creds.api_key)
    else:
        user = auth.login_with_password(creds.url, creds.username, creds.password)

    if user.get("username") and user["username"] != creds.username:
        raise CredentialsError(
            f"The server returned user {user['username']!r}, not {creds.username!r}."
        )

    name = account or creds.account or _default_account_name(creds.url, creds.username)
    entry = {"url": creds.url, "username": creds.username}
    # Only secrets the user themselves provided are saved, into the 0600
    # config. A secret from a credentials file stays in that file.
    if creds.password and creds.password_explicit:
        entry["password"] = creds.password
    if creds.api_key and creds.api_key_explicit:
        entry["api_key"] = creds.api_key
    if creds.credentials_file:
        entry["credentials_file"] = creds.credentials_file
    entry["profile"] = profile or creds.profile
    config_mod.save_account(name, entry)

    emit(ctx, {
        "account": name,
        "active": True,
        "url": creds.url,
        "username": creds.username,
        "role": user.get("role"),
        "password": masking.mask() if creds.password else None,
        "api_key": masking.mask() if creds.api_key else None,
        "credentials_file": creds.credentials_file,
    })


def _default_account_name(url: str, username: str) -> str:
    """A kubectl-context style default: the host, or host-username when taken."""
    host = urlparse(url).hostname or url
    existing = config_mod.get_account(config_mod.load_config(), host)
    if existing and existing.get("username") != username:
        return f"{host}-{username}"
    return host


@click.group("account")
def account_group():
    """Manage saved accounts, as in kubectl contexts."""


@account_group.command("list")
@click.pass_context
def account_list(ctx):
    """List saved accounts."""
    cfg = config_mod.load_config()
    active = config_mod.active_account_name(cfg)
    accounts = [
        _account_summary(name, entry, active)
        for name, entry in sorted(config_mod.get_accounts(cfg).items())
    ]
    emit(ctx, accounts)


@account_group.command("use")
@click.argument("name")
@click.pass_context
def account_use(ctx, name):
    """Make NAME the active account."""
    config_mod.set_active_account(name)
    emit(ctx, {"active_account": name})


@account_group.command("remove")
@click.argument("name")
@click.option("--yes", "-y", is_flag=True, default=False, help="Remove without confirmation.")
@click.pass_context
def account_remove(ctx, name, yes):
    """Remove a saved account from the config."""
    if not yes:
        if not has_terminal_stdin():
            raise ArgillaCLIError(
                "Removing an account needs confirmation and there is no terminal. Re-run with --yes."
            )
        click.confirm(f"Remove account {name!r} from the config? Continue?", default=False, abort=True)
    removed = config_mod.remove_account(name)
    emit(ctx, {"removed": name, "had_credentials_file": bool(removed.get("credentials_file"))})


@click.group("config")
def config_group():
    """Inspect the local config."""


@config_group.command("show")
@click.pass_context
def config_show(ctx):
    """Show the config with every secret masked."""
    cfg = config_mod.load_config()
    masked_accounts = {
        name: masking.mask_mapping(entry) for name, entry in config_mod.get_accounts(cfg).items()
    }
    out = {key: value for key, value in cfg.items() if key != "accounts"}
    out["accounts"] = masked_accounts
    out["config_path"] = str(config_mod.config_path())
    if ctx.obj.get("use_json"):
        emit(ctx, out)
        return
    click.echo(f"config_path: {out['config_path']}")
    click.echo(f"active_account: {cfg.get('active_account')}")
    for name, entry in masked_accounts.items():
        click.echo(f"account {name}:")
        for key, value in entry.items():
            click.echo(f"  {key}: {value}")
