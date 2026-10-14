"""setup, doctor, whoami, and info."""

import sys

import click

from argilla_cli import __version__
from argilla_cli import auth, masking
from argilla_cli.commands import account as account_mod
from argilla_cli.errors import ArgillaCLIError, CredentialsError
from argilla_cli.output import emit


@click.command("whoami")
@click.pass_context
def whoami_cmd(ctx):
    """Show the user the current credentials belong to."""
    creds = auth.resolve_credentials(ctx)
    user = _authenticated_user(creds)
    emit(ctx, {
        "account": creds.account,
        "id": user.get("id"),
        "username": user.get("username"),
        "first_name": user.get("first_name"),
        "last_name": user.get("last_name"),
        "role": user.get("role"),
        "url": creds.url,
        "profile": creds.profile,
    })


def _authenticated_user(creds: auth.Credentials) -> dict:
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


@click.command("info")
@click.pass_context
def info_cmd(ctx):
    """Show the CLI, config, and server versions."""
    import importlib.metadata
    import platform

    try:
        sdk_version = importlib.metadata.version("argilla")
    except importlib.metadata.PackageNotFoundError:
        sdk_version = "not installed"

    creds = auth.resolve_credentials(ctx)
    server_version = auth.server_version(creds.url) if creds.url else None

    emit(ctx, {
        "cli_version": __version__,
        "sdk_version": sdk_version,
        "python": platform.python_version(),
        "config_path": str(auth_config_path()),
        "active_account": creds.account,
        "url": creds.url,
        "server_version": server_version,
        "profile": creds.profile,
    })


def auth_config_path():
    from argilla_cli import config as config_mod
    return config_mod.config_path()


@click.command("doctor")
@click.pass_context
def doctor_cmd(ctx):
    """Check URL reachability, login, version match, and role."""
    checks: list[dict] = []

    creds = auth.resolve_credentials(ctx)

    # 1. URL reachable, and the server version while we are there.
    server_version = auth.server_version(creds.url) if creds.url else None
    if not creds.url:
        checks.append({"name": "url", "status": "fail",
                       "detail": "No server URL. Run `argilla-cli login` or pass --url."})
    elif server_version is None:
        checks.append({"name": "url", "status": "fail",
                       "detail": f"Could not reach {creds.url} (GET /api/v1/version)."})
    else:
        checks.append({"name": "url", "status": "ok", "detail": f"Reachable at {creds.url}."})

    # 2. Login works.
    user: dict = {}
    if creds.url and (creds.api_key or (creds.username and creds.password)):
        try:
            user = _authenticated_user(creds)
            checks.append({"name": "login", "status": "ok",
                           "detail": f"Logged in as {user.get('username')}."})
        except CredentialsError as exc:
            checks.append({"name": "login", "status": "fail", "detail": masking.redact_message(exc.message)})
    else:
        checks.append({"name": "login", "status": "fail",
                       "detail": "No credentials to try. Run `argilla-cli login`."})

    # 3. Server and SDK versions match (after stripping dev suffixes).
    if server_version is not None:
        import importlib.metadata
        try:
            sdk_version = importlib.metadata.version("argilla")
        except importlib.metadata.PackageNotFoundError:
            sdk_version = None
        if sdk_version is None:
            checks.append({"name": "version", "status": "fail",
                           "detail": "The argilla SDK is not installed."})
        else:
            server_release = _release(server_version)
            sdk_release = _release(sdk_version)
            if server_release == sdk_release:
                checks.append({"name": "version", "status": "ok",
                               "detail": f"Server {server_version}, SDK {sdk_version}."})
            else:
                checks.append({"name": "version", "status": "warn",
                               "detail": f"Server {server_version} and SDK {sdk_version} differ. "
                                          "Install the SDK version that matches the server."})
    else:
        checks.append({"name": "version", "status": "warn", "detail": "No server version to compare."})

    # 4. Role.
    if user:
        checks.append({"name": "role", "status": "ok", "detail": str(user.get("role"))})
    else:
        checks.append({"name": "role", "status": "warn", "detail": "Unknown without a working login."})

    ok = all(check["status"] != "fail" for check in checks)
    if not ok:
        if ctx.obj.get("use_json"):
            emit(ctx, {"ok": ok, "checks": checks})
        else:
            for check in checks:
                click.echo(f"{check['status'].upper():5} {check['name']}: {check['detail']}")
        raise ArgillaCLIError("doctor found a failing check.")
    if ctx.obj.get("use_json"):
        emit(ctx, {"ok": ok, "checks": checks})
        return
    for check in checks:
        click.echo(f"{check['status'].upper():5} {check['name']}: {check['detail']}")


def _release(version: str) -> str:
    """Strip dev/local suffixes: 2.8.0.dev0 and 2.8.0 are the same release."""
    base = version.split("+", 1)[0]
    parts = base.split(".dev", 1)[0].split("rc", 1)[0].split("a", 1)[0].split("b", 1)[0]
    return parts.rstrip(".")


@click.command("setup")
@click.pass_context
def setup_cmd(ctx):
    """Set up an account interactively. Needs a terminal."""
    if not account_mod.has_terminal_stdin():
        raise ArgillaCLIError(
            "setup is interactive and needs a terminal. Use `argilla-cli login --url URL --username NAME` instead."
        )
    url = click.prompt("Argilla server URL", default="http://localhost:6900")
    username = click.prompt("Username")
    password = click.prompt("Password", hide_input=True)
    masking.register_secrets([password])
    account = click.prompt("Account name", default=account_mod._default_account_name(url, username))
    profile = click.prompt("Permission profile", type=click.Choice(["full", "read-only"]), default="full")

    user = auth.login_with_password(url, username, password)
    from argilla_cli import config as config_mod
    config_mod.save_account(account, {
        "url": url,
        "username": username,
        "password": password,
        "profile": profile,
    })
    emit(ctx, {
        "account": account,
        "url": url,
        "username": username,
        "role": user.get("role"),
        "profile": profile,
        "password": masking.mask(),
    })
