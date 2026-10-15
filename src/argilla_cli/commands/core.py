"""setup, doctor, whoami, and info."""

import importlib.metadata
import platform
from typing import Any

import click

from argilla_cli import __version__, auth, masking
from argilla_cli import config as config_mod
from argilla_cli.commands import account as account_mod
from argilla_cli.errors import ArgillaCLIError, CredentialsError
from argilla_cli.output import emit, wants_json


@click.command("whoami")
@click.pass_context
def whoami_cmd(ctx: click.Context) -> None:
    """Show the user the current credentials belong to."""
    creds = auth.resolve_credentials(ctx)
    user = _authenticated_user(creds)
    emit(
        ctx,
        {
            "account": creds.account,
            "id": user.get("id"),
            "username": user.get("username"),
            "first_name": user.get("first_name"),
            "last_name": user.get("last_name"),
            "role": user.get("role"),
            "url": creds.url,
            "method": creds.method,
            "profile": creds.profile,
        },
    )


def _authenticated_user(creds: auth.Credentials) -> dict[str, Any]:
    """Log in (or verify the API key) and return the current user."""
    if not creds.url:
        raise CredentialsError("No server URL. Run `argilla-cli login` or pass --url.")
    if creds.api_key:
        return auth.login_with_api_key(creds.url, creds.api_key)
    if creds.username and creds.password:
        return auth.login_with_password(creds.url, creds.username, creds.password)
    raise CredentialsError(
        "No credentials for this account. Run `argilla-cli login`, or pass --password, "
        "--api-key, or --credentials-file."
    )


def _sdk_version() -> str:
    try:
        return importlib.metadata.version("argilla")
    except importlib.metadata.PackageNotFoundError:
        return "not installed"


@click.command("info")
@click.pass_context
def info_cmd(ctx: click.Context) -> None:
    """Show the CLI, config, and server versions."""
    creds = auth.resolve_credentials(ctx)
    server_version = auth.server_version(creds.url) if creds.url else None

    emit(
        ctx,
        {
            "cli_version": __version__,
            "sdk_version": _sdk_version(),
            "python": platform.python_version(),
            "config_path": str(config_mod.config_path()),
            "active_account": creds.account,
            "url": creds.url,
            "server_version": server_version,
            "profile": creds.profile,
        },
    )


def _check_url(creds: auth.Credentials) -> dict[str, str]:
    if not creds.url:
        return {"name": "url", "status": "fail", "detail": "No server URL. Run `argilla-cli login` or pass --url."}
    server_version = auth.server_version(creds.url)
    if server_version is None:
        return {"name": "url", "status": "fail", "detail": f"Could not reach {creds.url} (GET /api/v1/version)."}
    return {"name": "url", "status": "ok", "detail": f"Reachable at {creds.url}."}


def _check_login(creds: auth.Credentials) -> tuple[dict[str, str], dict[str, Any]]:
    """Check the login. Returns the check result and the user it authenticated."""
    if not creds.url or not (creds.api_key or (creds.username and creds.password)):
        return ({"name": "login", "status": "fail", "detail": "No credentials to try. Run `argilla-cli login`."}, {})
    try:
        user = _authenticated_user(creds)
    except CredentialsError as exc:
        return ({"name": "login", "status": "fail", "detail": masking.redact_message(exc.message)}, {})
    return ({"name": "login", "status": "ok", "detail": f"Logged in as {user.get('username')} ({creds.method})."}, user)


def _check_version(server_version: str | None) -> dict[str, str]:
    if server_version is None:
        return {"name": "version", "status": "warn", "detail": "No server version to compare."}
    sdk_version = _sdk_version()
    if sdk_version == "not installed":
        return {"name": "version", "status": "fail", "detail": "The argilla SDK is not installed."}
    if _release(server_version) == _release(sdk_version):
        return {"name": "version", "status": "ok", "detail": f"Server {server_version}, SDK {sdk_version}."}
    return {
        "name": "version",
        "status": "warn",
        "detail": f"Server {server_version} and SDK {sdk_version} differ. "
        "Install the SDK version that matches the server.",
    }


def _release(version: str) -> str:
    """Strip dev/local suffixes: 2.8.0.dev0 and 2.8.0 are the same release."""
    base = version.split("+", 1)[0]
    for marker in (".dev", "rc", "a", "b"):
        base = base.split(marker, 1)[0]
    return base.rstrip(".")


@click.command("doctor")
@click.pass_context
def doctor_cmd(ctx: click.Context) -> None:
    """Check URL reachability, login, version match, and role."""
    creds = auth.resolve_credentials(ctx)

    # The server version feeds both the url and the version checks.
    server_version = auth.server_version(creds.url) if creds.url else None
    url_check = _check_url(creds)
    login_check, user = _check_login(creds)
    version_check = _check_version(server_version)
    if user:
        role_check = {"name": "role", "status": "ok", "detail": str(user.get("role"))}
    else:
        role_check = {"name": "role", "status": "warn", "detail": "Unknown without a working login."}
    checks = [url_check, login_check, version_check, role_check]
    ok = all(check["status"] != "fail" for check in checks)

    if not ok:
        _print_doctor(ctx, checks)
        raise ArgillaCLIError("doctor found a failing check.")
    _print_doctor(ctx, checks)


def _print_doctor(ctx: click.Context, checks: list[dict[str, str]]) -> None:
    if wants_json(ctx):
        ok = all(check["status"] != "fail" for check in checks)
        emit(ctx, {"ok": ok, "checks": checks})
        return
    for check in checks:
        click.echo(f"{check['status'].upper():5} {check['name']}: {check['detail']}")


@click.command("setup")
@click.pass_context
def setup_cmd(ctx: click.Context) -> None:
    """Set up an account interactively. Needs a terminal."""
    if not account_mod.has_terminal_stdin():
        raise ArgillaCLIError(
            "setup is interactive and needs a terminal. Use `argilla-cli login --url URL --username NAME` instead."
        )
    url = click.prompt("Argilla server URL", default="http://localhost:6900")
    username = click.prompt("Username")
    password = click.prompt("Password", hide_input=True)
    masking.register_secrets([password])
    account = click.prompt("Account name", default=account_mod.default_account_name(url, username))
    profile = click.prompt("Permission profile", type=click.Choice(["full", "read-only"]), default="full")

    user = auth.login_with_password(url, username, password)
    config_mod.save_account(
        account,
        {
            "url": url,
            "username": username,
            "password": password,
            "profile": profile,
        },
    )
    emit(
        ctx,
        {
            "account": account,
            "url": url,
            "username": username,
            "role": user.get("role"),
            "profile": profile,
            "password": masking.mask(),
        },
    )
