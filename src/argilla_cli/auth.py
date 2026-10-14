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


def parse_env_file(path: Path | str) -> dict[str, str]:
    """Parse a small env file.

    Accepts KEY=VALUE lines, an optional `export ` prefix, single or double
    quotes around the value, blank lines, and # comments.
    """
    text = Path(path).read_text(encoding="utf-8")
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("\"", "'"):
            value = value[1:-1]
        if key:
            values[key] = value
    return values


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


def resolve_credentials(ctx=None, flags: dict | None = None, config_path=None) -> Credentials:
    """Resolve credentials: flags, then env, then the named or active account.

    ctx:    the click context; its local params (for instance --account on a
            subcommand) come first, then the group-level options in ctx.obj.
    flags:  explicit overrides, used by login for its own options.

    A credentials file passed as a flag counts as a flag: it wins over the
    environment for the fields it defines. An account that names a credentials
    file reads that file at run time, and file values win over the values
    stored in the account entry.
    """
    obj = (ctx.obj or {}) if ctx else {}
    params = ctx.params if ctx is not None else {}
    flags = flags or {}

    def pick(key: str, env_name: str | None):
        # Direct flags: command-local params, then explicit flag overrides,
        # then group-level options. The environment comes later, and the
        # account last -- see pick_with_account below.
        for source in (flags, params):
            value = source.get(key)
            if value:
                return value
        value = obj.get(key)
        if value:
            return value
        return None

    from argilla_cli import config as config_mod

    cfg = config_mod.load_config(config_path)
    account_name = pick("account", None) or config_mod.active_account_name(cfg)
    entry = config_mod.get_account(cfg, account_name) if account_name else None

    # A credentials file passed as a flag counts as a flag-level source.
    explicit_file = pick("credentials_file", None)
    explicit_values: dict[str, str] = {}
    if explicit_file:
        path = Path(explicit_file).expanduser()
        if not path.exists():
            raise CredentialsError(f"Credentials file {path} does not exist.")
        explicit_values = credentials_from_file(path)

    file_values: dict[str, str] = {}
    account_file: str | None = None
    if entry and entry.get("credentials_file"):
        account_file = str(Path(str(entry["credentials_file"])).expanduser())
        if not Path(account_file).exists():
            raise CredentialsError(
                f"Credentials file {account_file} does not exist (account {account_name!r})."
            )
        file_values = credentials_from_file(account_file)

    def pick_with_account(key: str, env_name: str):
        # Direct flags, then a credentials file passed as a flag, then the
        # environment, then the account: its credentials file first, then the
        # values stored in the entry.
        value = pick(key, None)
        explicit = bool(value)
        if not value and explicit_values.get(key):
            value = explicit_values[key]
        if not value and env_name:
            value = os.environ.get(env_name)
        if not value and entry:
            if entry_key_value := file_values.get(key):
                value = entry_key_value
            elif entry.get(key):
                value = entry[key]
        return value, explicit

    url, _ = pick_with_account("url", ENV_URL)
    username, _ = pick_with_account("username", ENV_USERNAME)
    password, password_explicit = pick_with_account("password", ENV_PASSWORD)
    api_key, api_key_explicit = pick_with_account("api_key", ENV_API_KEY)

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
    profile = obj.get("profile") or (entry or {}).get("profile") or "full"
    creds.profile = profile

    masking.register_secrets(creds.secrets())
    return creds


def http_client() -> httpx.Client:
    """A fresh client for the few raw calls the CLI makes itself."""
    return httpx.Client(timeout=DEFAULT_TIMEOUT, follow_redirects=True)


def _api(url: str, path: str) -> str:
    return url.rstrip("/") + path


def request_token(url: str, username: str, password: str, client: httpx.Client | None = None) -> str:
    """Exchange a username and password for an access token.

    POST /api/v1/token takes form fields, per the OAuth2 password flow the
    Argilla server implements.
    """
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
    if response.status_code != 200:
        raise CredentialsError(
            f"Login failed: HTTP {response.status_code} from {url}: "
            f"{masking.redact_message(response.text[:200])}"
        )
    token = response.json().get("access_token")
    if not token:
        raise CredentialsError("Login response carried no access_token.")
    masking.register_secrets([token])
    return token


def request_me(
    url: str,
    token: str | None = None,
    api_key: str | None = None,
    client: httpx.Client | None = None,
) -> dict:
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
    if response.status_code != 200:
        raise CredentialsError(
            f"Could not read the current user: HTTP {response.status_code}: "
            f"{masking.redact_message(response.text[:200])}"
        )
    return response.json()


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
            payload = response.json()
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


def login_with_password(url: str, username: str, password: str, client: httpx.Client | None = None) -> dict:
    """Full password login: token, then the user. The API key stays in memory."""
    masking.register_secrets([password])
    token = request_token(url, username, password, client=client)
    user = request_me(url, token=token, client=client)
    api_key = user.get("api_key")
    if api_key:
        masking.register_secrets([api_key])
    return user


def login_with_api_key(url: str, api_key: str, client: httpx.Client | None = None) -> dict:
    """Verify an API key and return the user it belongs to."""
    user = request_me(url, api_key=api_key, client=client)
    return user


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
    creds.api_key = api_key
    return api_key


def build_client(creds: Credentials, client: httpx.Client | None = None):
    """Construct the official SDK client for these credentials."""
    import argilla as rg  # Imported late: login and doctor do not need the SDK.

    api_key = ensure_api_key(creds, client=client)
    masking.register_secrets([api_key])
    try:
        return rg.Argilla(api_url=creds.url, api_key=api_key)
    except Exception as exc:  # noqa: BLE001 - surface SDK construction errors redacted
        raise CredentialsError(f"Could not build the Argilla client: {masking.redact_message(str(exc))}") from exc
