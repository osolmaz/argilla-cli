"""Config and named-account storage for argilla-cli.

Modeled on discli's config handling: one JSON file, the directory 0700 and the
file 0600. The fchmod() runs while the file is still empty, so a secret is
never on disk at a looser mode. The only file that may hold a password is this
config, and only when the user saved it there (a credentials file referenced by
an account keeps the secret outside the config entirely).
"""

import contextlib
import json
import os
import sys
from pathlib import Path
from typing import Any

from argilla_cli.errors import ConfigError

DEFAULT_CONFIG_DIR = Path.home() / ".argilla-cli"
DEFAULT_CONFIG_PATH = DEFAULT_CONFIG_DIR / "config.json"

# Keys a single account entry may hold.
ACCOUNT_KEYS = ("url", "username", "password", "api_key", "credentials_file", "profile")
VALID_PROFILES = ("full", "read-only")


def _warn(message: str) -> None:
    sys.stderr.write(f"Warning: {message}\n")
    sys.stderr.flush()


def config_path() -> Path:
    """Where the config lives: $ARGILLA_CLI_CONFIG or ~/.argilla-cli/config.json."""
    env = os.environ.get("ARGILLA_CLI_CONFIG")
    if env:
        return Path(env).expanduser()
    return DEFAULT_CONFIG_PATH


def load_config(path: Path | None = None) -> dict[str, Any]:
    path = path if path is not None else config_path()
    if not path.exists():
        return {}
    try:
        loaded: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"could not read config at {path}: {exc}") from exc
    return loaded if isinstance(loaded, dict) else {}


def save_config(data: dict[str, Any], path: Path | None = None) -> None:
    """Write the whole config, creating the file 0600 and the directory 0700.

    The write is atomic: a temp file in the same directory receives the
    content and then replaces the config, so a crash or a second writer can
    never leave a half-written JSON file behind.
    """
    path = path if path is not None else config_path()
    parent_existed = path.parent.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    if os.name != "nt" and not parent_existed:
        # Tighten the mode only on a directory this call created; chmod on an
        # existing directory (say $HOME) is not ours to change.
        try:
            path.parent.chmod(0o700)
        except OSError as exc:
            _warn(f"could not set permissions on {path.parent}: {exc}")

    content = json.dumps(data, indent=2) + "\n"

    if os.name == "nt":
        path.write_text(content, encoding="utf-8")
        return

    temp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        fd = os.open(temp_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
        os.replace(temp_path, path)
    except OSError as exc:
        with contextlib.suppress(OSError):
            os.unlink(temp_path)
        raise ConfigError(f"could not write config at {path}: {exc}") from exc


def get_accounts(config: dict[str, Any]) -> dict[str, Any]:
    accounts = config.get("accounts")
    return accounts if isinstance(accounts, dict) else {}


def get_account(config: dict[str, Any], name: str) -> dict[str, Any] | None:
    entry = get_accounts(config).get(name)
    return entry if isinstance(entry, dict) else None


def active_account_name(config: dict[str, Any]) -> str | None:
    name = config.get("active_account")
    return name if isinstance(name, str) and name else None


def validate_account_name(name: str) -> str:
    name = (name or "").strip()
    if not name or any(ch.isspace() for ch in name):
        raise ConfigError(f"Invalid account name {name!r}: pick a short name without spaces, as in kubectl contexts.")
    return name


def validate_account_entry(entry: dict[str, Any]) -> dict[str, Any]:
    """Check one account entry, returning only the known keys that are set."""
    url = entry.get("url")
    if url is not None:
        validate_server_url(str(url))
    profile = entry.get("profile") or "full"
    if profile not in VALID_PROFILES:
        raise ConfigError(f"Account profile must be one of {', '.join(VALID_PROFILES)}, got {profile!r}.")
    cleaned = {key: entry[key] for key in ACCOUNT_KEYS if entry.get(key) is not None}
    cleaned.setdefault("profile", profile)
    return cleaned


def validate_server_url(url: str) -> str:
    """A server URL is a bare origin: no credentials, query, or fragment."""
    from urllib.parse import urlparse

    if not url.startswith(("http://", "https://")):
        raise ConfigError(f"Server URL must start with http:// or https://, got {url!r}.")
    parsed = urlparse(url)
    if parsed.username or parsed.password:
        raise ConfigError("A server URL must not carry a username or password; pass them with --username/--password.")
    if parsed.query or parsed.fragment:
        raise ConfigError(f"A server URL must not carry a query string or fragment, got {url!r}.")
    return url


def save_account(name: str, entry: dict[str, Any], path: Path | None = None) -> None:
    """Create or update one account entry and make it the active account.

    Secret fields (api_key, password, credentials_file) are replaced, not
    merged: a new login that carries no secret must not keep the previous
    login's secret alive under the same name.
    """
    name = validate_account_name(name)
    entry = validate_account_entry(entry)
    config = load_config(path)
    accounts = get_accounts(config)
    existing = accounts.get(name) or {}
    for key in ("api_key", "password", "credentials_file"):
        existing.pop(key, None)
    existing.update(entry)
    accounts[name] = existing
    config["accounts"] = accounts
    config["active_account"] = name
    save_config(config, path)


def set_active_account(name: str, path: Path | None = None) -> None:
    name = validate_account_name(name)
    config = load_config(path)
    if name not in get_accounts(config):
        raise ConfigError(f"No account named {name!r}. Run `argilla-cli account list` to see accounts.")
    config["active_account"] = name
    save_config(config, path)


def remove_account(name: str, path: Path | None = None) -> dict[str, Any]:
    """Remove an account. Returns the removed entry."""
    name = validate_account_name(name)
    config = load_config(path)
    accounts = get_accounts(config)
    if name not in accounts:
        raise ConfigError(f"No account named {name!r}. Run `argilla-cli account list` to see accounts.")
    removed: dict[str, Any] = accounts.pop(name)
    config["accounts"] = accounts
    if active_account_name(config) == name:
        # Never pick a new active account behind the user's back: the next
        # one by name could be a full-profile production account.
        config["active_account"] = None
    save_config(config, path)
    return removed
