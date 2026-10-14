"""Errors for argilla-cli.

Everything user-facing is a ClickException, so click prints one clean,
redacted message and exits non-zero instead of a traceback. Redaction happens
in format_message(), so every path that prints an error -- the console entry
point and click's own standalone handling alike -- scrubs registered secrets.
"""

import click


class ArgillaCLIError(click.ClickException):
    """Base class for every error argilla-cli raises on purpose."""

    def format_message(self) -> str:
        from argilla_cli import masking

        return masking.redact_message(self.message)


class ConfigError(ArgillaCLIError):
    """The config file is missing a needed piece or is unreadable."""


class CredentialsError(ArgillaCLIError):
    """Credentials are missing, wrong, or the server rejected them."""


class ProfileDeniedError(ArgillaCLIError):
    """The active permission profile refuses this command."""


class NotFoundError(ArgillaCLIError):
    """The named resource does not exist on the server."""
