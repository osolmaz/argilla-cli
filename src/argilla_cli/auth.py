"""Credential resolution, the login flow, and Argilla client construction.

Resolution order, per field: flags, then the environment (ARGILLA_API_URL,
ARGILLA_USERNAME, ARGILLA_PASSWORD, ARGILLA_API_KEY), then the active (or
named) account in the config. An account that names a credentials file reads
that file at run time -- the secret stays where it is and is never copied into
the config or this process's arguments.

At run time the CLI logs in with the password through POST /api/v1/token and
reads the user's API key from GET /api/v1/me, then hands the key to
rg.Argilla(api_url=..., api_key=...). Tokens and keys stay in memory only.
"""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import click
import httpx

from argilla_cli import masking
from argilla_cli.errors import CredentialsError

ENV_URL = "ARGILLA_API_URL"
ENV_USERNAME = "ARGILLA_USERNAME"
ENV_PASSWORD = "ARGILLA_PASSWORD"
ENV_API_KEY = "ARGILLA_API_KEY"

# Names accepted inside a credentials file. The ARGILLA_* name wins when both
# spellings appear.
_ENV_FILE_KEYS = {
    "url": ("ARGILLA_API_URL", "URL"),
    "username": ("ARGILLA_USERNAME", "USERNAME"),
    "password": ("ARGILLA_PASSWORD", "PASSWORD"),
    "api_key": ("ARGILLA_API_KEY", "API_KEY"),
}

DEFAULT_TIMEOUT = 30.0


@dataclass
class Credentials:
    url: str | None = None
    username: str | None = None
    password: str | None = None
    api_key: str | None = None
    account: str | None = None
    profile: str = "full"
    credentials_file: str | None = None
    # True when the user themselves provided the secret this run (flag, env,
    # or prompt). Secrets read from a credentials file or the config are never
    # written back into the config: the file stays where it is.
    password_explicit: bool = False
    api_key_explicit: bool = False

    def secrets(self) -> list[str]:
        """Every secret value these credentials hold, for redaction."""
        return [value for value in (self.password, self.api_key) if value]

    @property
    def method(self) -> str | None:
        """The authentication method in force: the API key wins over a password."""
        if self.api_key:
            return "api_key"
        if self.password:
            return "password"
        return None


def parse_env_file(path: Path | str) -> dict[str, str]:
    """Parse a small env file.

    Accepts KEY=VALUE lines, an optional `export ` prefix, single or double
    quotes around the value, blank lines, and # comments.
    """
    text = Path(path).read_text(encoding="utf-8")
    values: dict[str, str] = {}
    for raw in text.splitlines():
        parsed = _parse_env_line(raw)
        if parsed is not None:
            key, value = parsed
            values[key] = value
    return values


def _parse_env_line(raw: str) -> tuple[str, str] | None:
    line = raw.strip()
    if not line or line.startswith("#"):
        return None
    if line.startswith("export "):
        line = line[len("export ") :].lstrip()
    if "=" not in line:
        return None
    key, _, value = line.partition("=")
    key = key.strip()
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
        value = value[1:-1]
    if not key:
        return None
    return key, value


def credentials_from_file(path: Path | str) -> dict[str, str]:
    """Read credentials out of an env file.

    Returns only the fields the file defines, under our canonical names.
    """
    values = parse_env_file(path)
    out: dict[str, str] = {}
    for field_name, keys in _ENV_FILE_KEYS.items():
        for key in keys:
            value = values.get(key)
            if value:
                out[field_name] = value
                break
    return out


def _read_credentials_file(path_text: str, label: str) -> dict[str, str]:
    path = Path(path_text).expanduser()
    if not path.exists():
        raise CredentialsError(f"Credentials file {path} does not exist{label}.")
    return credentials_from_file(path)


def _resolve_field(
    key: str,
    env_name: str | None,
    sources: list[dict[str, Any]],
    explicit_values: dict[str, str],
    entry: dict[str, Any] | None,
    file_values: dict[str, str],
) -> tuple[str | None, bool]:
    """Resolve one credential field: direct sources, env, then the account.

    Returns the value and whether the user themselves provided it this run
    (flag or environment, and so it may be saved into the config).
    """
    value: str | None = None
    for source in sources:
        found = source.get(key)
        if found:
            value = str(found)
            break
    explicit = value is not None
    if value is None and explicit_values.get(key):
        value = explicit_values[key]
    if value is None and env_name:
        value = os.environ.get(env_name)
        if value:
            # An environment secret is as much the user's own choice as a
            # flag: `ARGILLA_API_KEY=... login` must be able to save it.
            explicit = True
    if value is None and entry is not None:
        value = file_values.get(key) or entry.get(key) or None
        if isinstance(value, str) is False and value is not None:
            value = str(value)
    return value, explicit


def _source_level(
    key: str, sources: list[dict[str, Any]], explicit_values: dict[str, str], env_name: str | None
) -> str:
    """Where a field came from: 'direct' (flag, env, or credentials file) or 'account'."""
    for source in sources:
        if source.get(key):
            return "direct"
    if explicit_values.get(key) or (env_name and os.environ.get(env_name)):
        return "direct"
    return "account"


def _refuse_mismatched_secret_url(
    url: str,
    entry: dict[str, Any] | None,
    account_name: str | None,
    direct_sources: list[dict[str, Any]],
    explicit_values: dict[str, str],
    api_key: str | None,
    password: str | None,
) -> None:
    """Refuse an account secret when the URL names a different server.

    Credentials resolve as a set, not per field: a URL from a flag or the
    environment must never borrow a secret stored for another server.
    """
    url_level = _source_level("url", direct_sources, explicit_values, ENV_URL)
    if url_level != "direct" or entry is None:
        return
    entry_url = entry.get("url")
    for secret_key, env_name, secret_value in (
        ("api_key", ENV_API_KEY, api_key),
        ("password", ENV_PASSWORD, password),
    ):
        if secret_value is None:
            continue
        if _source_level(secret_key, direct_sources, explicit_values, env_name) != "account":
            continue
        if entry_url and str(entry_url) != url:
            raise CredentialsError(
                f"The URL {url} does not match the URL of the account in use"
                f"{f' ({account_name})' if account_name else ''}, so its stored secret must not be sent"
                " there. Pass --api-key or --username and --password for this server, or drop --url."
            )


def resolve_credentials(
    ctx: click.Context | None = None,
    flags: dict[str, Any] | None = None,
    config_path: Path | None = None,
    account_reference: bool = True,
) -> Credentials:
    """Resolve credentials: flags, then env, then the named or active account.

    ctx:    the click context; its local params (for instance --account on a
            subcommand) come first, then the group-level options in ctx.obj.
    flags:  explicit overrides, used by login for its own options.
    account_reference: when False, an --account parameter names a SAVE target
            (as on `login`), not an account to resolve credentials from.
    """
    from argilla_cli import config as config_mod

    obj: dict[str, Any] = (ctx.obj or {}) if ctx is not None else {}
    params: dict[str, Any] = dict(ctx.params) if ctx is not None else {}
    if not account_reference:
        params.pop("account", None)
    direct_sources = [flags or {}, params, obj]

    cfg = config_mod.load_config(config_path)
    named_account = _direct_value("account", direct_sources)
    account_name = named_account or config_mod.active_account_name(cfg)
    entry = config_mod.get_account(cfg, account_name) if account_name else None
    if named_account and entry is None:
        raise CredentialsError(f"No account named {named_account!r}. Run `argilla-cli account list` to see accounts.")

    # A credentials file passed as a flag counts as a flag-level source.
    explicit_file = _direct_value("credentials_file", direct_sources)
    explicit_values: dict[str, str] = {}
    if explicit_file:
        explicit_values = _read_credentials_file(explicit_file, "")

    account_file: str | None = None
    file_values: dict[str, str] = {}
    if entry and entry.get("credentials_file"):
        account_file = str(entry["credentials_file"])
        file_values = _read_credentials_file(account_file, f" (account {account_name!r})")

    url, _ = _resolve_field("url", ENV_URL, direct_sources, explicit_values, entry, file_values)
    username, _ = _resolve_field("username", ENV_USERNAME, direct_sources, explicit_values, entry, file_values)
    password, password_explicit = _resolve_field(
        "password", ENV_PASSWORD, direct_sources, explicit_values, entry, file_values
    )
    api_key, api_key_explicit = _resolve_field(
        "api_key", ENV_API_KEY, direct_sources, explicit_values, entry, file_values
    )

    # Credentials resolve as a set, not per field: see the helper.
    _refuse_mismatched_secret_url(url or "", entry, account_name, direct_sources, explicit_values, api_key, password)

    creds = Credentials(
        url=url,
        username=username,
        password=password,
        api_key=api_key,
        account=account_name,
        credentials_file=explicit_file or account_file,
        password_explicit=password_explicit,
        api_key_explicit=api_key_explicit,
    )
    # --profile can only tighten: a read-only account stays read-only even
    # when the invocation asks for full.
    entry_profile = (entry or {}).get("profile") or "full"
    override = obj.get("profile")
    creds.profile = "read-only" if (entry_profile == "read-only" or override == "read-only") else "full"

    masking.register_secrets(creds.secrets())
    return creds


def _direct_value(key: str, sources: list[dict[str, Any]]) -> str | None:
    for source in sources:
        found = source.get(key)
        if found:
            return str(found)
    return None


def http_client() -> httpx.Client:
    """A fresh client for the few raw calls the CLI makes itself.

    Redirects are refused: httpx strips only the Authorization header on a
    cross-origin redirect, so following one could forward the API key or the
    password form to whatever host the Location names.
    """
    return httpx.Client(timeout=DEFAULT_TIMEOUT, follow_redirects=False)


def _refuse_redirect(response: httpx.Response, url: str) -> None:
    """Raise instead of following a redirect: it may be fishing for credentials."""
    if not (300 <= response.status_code < 400):
        return
    location = response.headers.get("location", "")
    # A query string can smuggle secrets back into a message: drop it.
    safe_location = masking.redact_message(location.split("?", 1)[0])
    raise CredentialsError(
        f"The server at {url} answered with a redirect to {safe_location or 'another location'}"
        " instead of the Argilla API. Check the URL and try the redirect target directly."
    )


def _api(url: str, path: str) -> str:
    return url.rstrip("/") + path


def request_token(url: str, username: str, password: str, client: httpx.Client | None = None) -> str:
    """Exchange a username and password for an access token.

    POST /api/v1/token takes form fields, per the OAuth2 password flow the
    Argilla server implements.
    """
    masking.register_secrets([password])
    own = client is None
    client = client or http_client()
    try:
        response = client.post(
            _api(url, "/api/v1/token"),
            data={"username": username, "password": password},
        )
    except httpx.HTTPError as exc:
        raise CredentialsError(f"Could not reach {url}: {masking.redact_message(str(exc))}") from exc
    finally:
        if own:
            client.close()
    if response.status_code == 401:
        raise CredentialsError(f"Invalid username or password for {url}.")
    _refuse_redirect(response, url)
    if response.status_code not in (200, 201):
        # The body may carry the access token: never quote it back.
        raise CredentialsError(f"Login failed: HTTP {response.status_code} from {url}.")
    try:
        payload: Any = response.json()
    except ValueError as exc:
        raise CredentialsError(f"The server at {url} did not answer with an Argilla API response.") from exc
    token = payload.get("access_token")
    if not token:
        raise CredentialsError("Login response carried no access_token.")
    masking.register_secrets([token])
    return str(token)


def request_me(
    url: str,
    token: str | None = None,
    api_key: str | None = None,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    """Fetch the current user with GET /api/v1/me."""
    headers: dict[str, str] = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    elif api_key:
        headers["X-Argilla-Api-Key"] = api_key
    else:
        raise CredentialsError("Checking the user needs a token or an API key.")

    own = client is None
    client = client or http_client()
    try:
        response = client.get(_api(url, "/api/v1/me"), headers=headers)
    except httpx.HTTPError as exc:
        raise CredentialsError(f"Could not reach {url}: {masking.redact_message(str(exc))}") from exc
    finally:
        if own:
            client.close()
    if response.status_code == 401:
        raise CredentialsError(f"The server at {url} rejected these credentials (HTTP 401).")
    _refuse_redirect(response, url)
    if response.status_code != 200:
        raise CredentialsError(f"Could not read the current user: HTTP {response.status_code} from {url}.")
    try:
        user: Any = response.json()
    except ValueError as exc:
        raise CredentialsError(f"The server at {url} did not answer with an Argilla API response.") from exc
    return user if isinstance(user, dict) else {}


def server_version(url: str, client: httpx.Client | None = None) -> str | None:
    """Read the server version from GET /api/v1/version, or None when unreachable.

    The endpoint has returned a bare version string and a JSON object at
    different times, so accept both.
    """
    own = client is None
    client = client or http_client()
    try:
        try:
            response = client.get(_api(url, "/api/v1/version"))
        except httpx.HTTPError:
            return None
        if response.status_code != 200:
            return None
        body = response.text.strip().strip('"')
        try:
            payload: Any = response.json()
            if isinstance(payload, dict):
                payload = payload.get("version")
            if isinstance(payload, str):
                body = payload
        except ValueError:
            pass
        return body or None
    finally:
        if own:
            client.close()


def login_with_password(url: str, username: str, password: str, client: httpx.Client | None = None) -> dict[str, Any]:
    """Full password login: token, then the user. The API key stays in memory."""
    token = request_token(url, username, password, client=client)
    user = request_me(url, token=token, client=client)
    api_key = user.get("api_key")
    if api_key:
        masking.register_secrets([api_key])
    return user


def login_with_api_key(url: str, api_key: str, client: httpx.Client | None = None) -> dict[str, Any]:
    """Verify an API key and return the user it belongs to."""
    return request_me(url, api_key=api_key, client=client)


def ensure_api_key(creds: Credentials, client: httpx.Client | None = None) -> str:
    """Return an API key for these credentials, logging in with the password if needed."""
    if creds.api_key:
        return creds.api_key
    if not (creds.url and creds.username and creds.password):
        raise CredentialsError(
            "No API key and no full username and password pair for this account. "
            "Run `argilla-cli login` again, or pass --api-key or --credentials-file."
        )
    user = login_with_password(creds.url, creds.username, creds.password, client=client)
    api_key = user.get("api_key")
    if not api_key:
        raise CredentialsError(
            "The server did not return an API key for this user, so the SDK client cannot authenticate."
        )
    creds.api_key = str(api_key)
    return creds.api_key


def build_client(creds: Credentials, client: httpx.Client | None = None) -> Any:  # noqa: ANN401 -- the SDK client type is the external boundary
    """Construct the official SDK client for these credentials."""
    import argilla as rg  # Imported late: login and doctor do not need the SDK.

    api_key = ensure_api_key(creds, client=client)
    masking.register_secrets([api_key])
    try:
        return rg.Argilla(api_url=creds.url, api_key=api_key)
    except Exception as exc:  # any SDK construction failure is a user-facing credential error
        raise CredentialsError(f"Could not build the Argilla client: {masking.redact_message(str(exc))}") from exc
