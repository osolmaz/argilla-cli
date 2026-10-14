"""Config and named-account storage for argilla-cli.

Modeled on discli's config handling: one JSON file, the directory 0700 and the
file 0600. The fchmod() runs while the file is still empty, so a secret is
never on disk at a looser mode. The only file that may hold a password is this
config, and only when the user saved it there (a credentials file referenced by
an account keeps the secret outside the config entirely).
"""

import json
import os
import sys
from pathlib import Path

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


def load_config(path: Path | None = None) -> dict:
    path = path if path is not None else config_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"could not read config at {path}: {exc}") from exc


def save_config(data: dict, path: Path | None = None) -> None:
    """Write the whole config, creating the file 0600 and the directory 0700."""
    path = path if path is not None else config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        try:
            path.parent.chmod(0o700)
        except OSError as exc:
            _warn(f"could not set permissions on {path.parent}: {exc}")

    content = json.dumps(data, indent=2) + "\n"

    if os.name == "nt":
        path.write_text(content, encoding="utf-8")
        return

    # O_CREAT's mode argument is ignored when the file already exists, so an
    # inherited 0644 config would survive the write. fchmod() after the
    # O_TRUNC is what actually tightens it -- and it runs while the file is
    # still empty, so the secret is never on disk at the looser mode.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.fchmod(fd, 0o600)
    except OSError as exc:
        # Worth continuing: a secret in a loose-moded file still beats no
        # saved login at all, as long as the weakened guarantee is not silent.
        _warn(f"could not set permissions on {path}: {exc}")

    try:
        handle = os.fdopen(fd, "w", encoding="utf-8")
    except BaseException:
        # The only window where nothing else owns the descriptor. Once
        # fdopen() returns, the file object owns it and closing here too
        # would be a double close.
        os.close(fd)
        raise
    with handle:
        handle.write(content)


def get_accounts(config: dict) -> dict:
    accounts = config.get("accounts")
    return accounts if isinstance(accounts, dict) else {}


def get_account(config: dict, name: str) -> dict | None:
    entry = get_accounts(config).get(name)
    return entry if isinstance(entry, dict) else None


def active_account_name(config: dict) -> str | None:
    name = config.get("active_account")
    return name if isinstance(name, str) and name else None


def validate_account_name(name: str) -> str:
    name = (name or "").strip()
    if not name or any(ch.isspace() for ch in name):
        raise ConfigError(
            f"Invalid account name {name!r}: pick a short name without spaces, as in kubectl contexts."
        )
    return name


def validate_account_entry(entry: dict) -> dict:
    """Check one account entry, returning only the known keys that are set."""
    url = entry.get("url")
    if url is not None and not (isinstance(url, str) and url.startswith(("http://", "https://"))):
        raise ConfigError(f"Account url must start with http:// or https://, got {url!r}.")
    profile = entry.get("profile") or "full"
    if profile not in VALID_PROFILES:
        raise ConfigError(
            f"Account profile must be one of {', '.join(VALID_PROFILES)}, got {profile!r}."
        )
    cleaned = {key: entry[key] for key in ACCOUNT_KEYS if entry.get(key) is not None}
    cleaned.setdefault("profile", profile)
    return cleaned


def save_account(name: str, entry: dict, path: Path | None = None) -> None:
    """Create or update one account entry and make it the active account."""
    name = validate_account_name(name)
    entry = validate_account_entry(entry)
    config = load_config(path)
    accounts = get_accounts(config)
    existing = accounts.get(name) or {}
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


def remove_account(name: str, path: Path | None = None) -> dict:
    """Remove an account. Returns the removed entry."""
    name = validate_account_name(name)
    config = load_config(path)
    accounts = get_accounts(config)
    if name not in accounts:
        raise ConfigError(f"No account named {name!r}. Run `argilla-cli account list` to see accounts.")
    entry = accounts.pop(name)
    config["accounts"] = accounts
    if active_account_name(config) == name:
        remaining = sorted(accounts)
        config["active_account"] = remaining[0] if remaining else None
    save_config(config, path)
    return entry
