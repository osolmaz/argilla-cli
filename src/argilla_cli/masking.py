"""Secret hygiene: register secret values once per run, redact everywhere.

No password, token, or API key may reach output, logs, or error messages.
Commands register the secret values they hold; `redact()` scrubs every message
the CLI prints, and `mask_mapping()` scrubs structured output such as
`config show`. Masks are total: no prefix, suffix, or length is revealed.
"""

import re
from collections.abc import Container, Iterable
from typing import Any

REDACTED = "***"

# Keys whose values are secrets in structured data.
SECRET_KEYS: Container[str] = frozenset({"password", "api_key", "token", "access_token", "authorization"})

_registered: list[str] = []


def register_secrets(values: Iterable[str]) -> None:
    """Remember secret values to redact from every later message."""
    for value in values:
        if value and value not in _registered:
            _registered.append(value)


def reset_registered() -> None:
    """Forget all registered secrets. For tests only."""
    _registered.clear()


def registered_secrets() -> list[str]:
    return list(_registered)


def mask() -> str:
    """A total mask for a secret value."""
    return REDACTED


def mask_mapping(mapping: dict[str, Any], keys: Container[str] = SECRET_KEYS) -> dict[str, Any]:
    """Copy a mapping with every secret-keyed value replaced by the mask.

    Empty values stay empty: there is nothing to hide.
    """
    return {key: (REDACTED if key in keys and value else value) for key, value in mapping.items()}


def redact(text: str) -> str:
    """Remove every registered secret value from a message."""
    for secret in _registered:
        if secret in text:
            text = text.replace(secret, REDACTED)
    return text


# A URL query or fragment never carries Argilla credentials, but tokens and
# keys could end up in a pasted URL; scrub whole query strings too.
_URL_QUERY = re.compile(r"(https?://\S*\?)\S*")


def redact_message(text: str) -> str:
    """Redact registered secrets and any URL query string from a message."""
    text = redact(text)
    return _URL_QUERY.sub(r"\1<redacted>", text)
