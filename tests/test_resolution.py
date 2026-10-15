"""Tests for the credential resolution order: flags, env, then the account."""

import json

import pytest

from argilla_cli import auth


@pytest.fixture()
def account_config(isolated_config):
    """A stored account with a credentials file, no stored secrets."""
    creds_file = isolated_config.parent / "studio.env"
    creds_file.parent.mkdir(parents=True, exist_ok=True)
    creds_file.write_text("USERNAME=fileuser\nPASSWORD=filepass\n")
    isolated_config.write_text(
        json.dumps(
            {
                "active_account": "studio",
                "accounts": {
                    "studio": {
                        "url": "https://studio.example.org",
                        "username": "storeduser",
                        "credentials_file": str(creds_file),
                        "profile": "read-only",
                    }
                },
            }
        )
    )
    return isolated_config


class FakeCtx:
    def __init__(self, obj=None, params=None):
        self.obj = obj or {}
        self.params = params or {}


def test_flags_beat_everything(account_config):
    creds = auth.resolve_credentials(
        FakeCtx(
            obj={"url": "https://flag.example.org", "username": "flaguser", "password": "flagpass"},
        )
    )
    assert creds.url == "https://flag.example.org"
    assert creds.username == "flaguser"
    assert creds.password == "flagpass"


def test_env_url_with_a_different_servers_secret_is_refused(account_config, monkeypatch):
    """Credentials resolve as a set: an env URL must not borrow the stored secret."""
    monkeypatch.setenv("ARGILLA_API_URL", "https://env.example.org")
    monkeypatch.setenv("ARGILLA_USERNAME", "envuser")
    monkeypatch.delenv("ARGILLA_PASSWORD", raising=False)
    monkeypatch.delenv("ARGILLA_API_KEY", raising=False)
    with pytest.raises(auth.CredentialsError, match="must not be sent"):
        auth.resolve_credentials(FakeCtx())


def test_env_url_matching_the_account_url_keeps_its_secret(account_config, monkeypatch):
    """The same URL through the environment is only a spelling change."""
    monkeypatch.setenv("ARGILLA_API_URL", "https://studio.example.org")
    monkeypatch.delenv("ARGILLA_USERNAME", raising=False)
    monkeypatch.delenv("ARGILLA_PASSWORD", raising=False)
    monkeypatch.delenv("ARGILLA_API_KEY", raising=False)
    creds = auth.resolve_credentials(FakeCtx())
    assert creds.url == "https://studio.example.org"
    assert creds.password == "filepass"


def test_account_credentials_file_read_at_runtime(account_config):
    creds = auth.resolve_credentials(FakeCtx())
    assert creds.url == "https://studio.example.org"
    # The file wins over the stored username: the file is the runtime source.
    assert creds.username == "fileuser"
    assert creds.password == "filepass"
    assert creds.credentials_file.endswith("studio.env")
    assert creds.account == "studio"


def test_named_account_beats_active(account_config):
    config_data = json.loads(account_config.read_text())
    config_data["accounts"]["other"] = {"url": "https://other.example.org", "username": "otheruser"}
    account_config.write_text(json.dumps(config_data))
    creds = auth.resolve_credentials(FakeCtx(params={"account": "other"}))
    assert creds.account == "other"
    assert creds.url == "https://other.example.org"


def test_explicit_credentials_file_flag_wins_over_env(account_config, monkeypatch, tmp_path):
    f = tmp_path / "explicit.env"
    f.write_text("PASSWORD=explicitpass\n")
    monkeypatch.setenv("ARGILLA_PASSWORD", "envpass")
    creds = auth.resolve_credentials(FakeCtx(obj={"credentials_file": str(f)}))
    assert creds.password == "explicitpass"


def test_missing_credentials_file_is_a_clean_error(account_config):
    config_data = json.loads(account_config.read_text())
    config_data["accounts"]["studio"]["credentials_file"] = "/does/not/exist.env"
    account_config.write_text(json.dumps(config_data))
    with pytest.raises(auth.CredentialsError, match="does not exist"):
        auth.resolve_credentials(FakeCtx())


def test_no_credentials_anywhere_gives_guidance(isolated_config):
    creds = auth.resolve_credentials(FakeCtx())
    assert creds.url is None
    assert creds.password is None


def test_profile_from_account_entry_and_flag_override(account_config):
    creds = auth.resolve_credentials(FakeCtx())
    assert creds.profile == "read-only"
    # --profile can only tighten: a read-only account stays read-only.
    creds = auth.resolve_credentials(FakeCtx(obj={"profile": "full"}))
    assert creds.profile == "read-only"
    creds = auth.resolve_credentials(FakeCtx(obj={"profile": "read-only"}))
    assert creds.profile == "read-only"


def test_secrets_registered_for_redaction(account_config):
    creds = auth.resolve_credentials(FakeCtx())
    secrets = auth.masking.registered_secrets()
    assert creds.password in secrets


def test_account_entry_without_file_uses_stored_values(isolated_config):
    isolated_config.parent.mkdir(parents=True, exist_ok=True)
    isolated_config.write_text(
        json.dumps(
            {
                "active_account": "plain",
                "accounts": {
                    "plain": {
                        "url": "https://plain.example.org",
                        "username": "plainuser",
                        "password": "plainpass",
                    }
                },
            }
        )
    )
    creds = auth.resolve_credentials(FakeCtx())
    assert creds.url == "https://plain.example.org"
    assert creds.username == "plainuser"
    assert creds.password == "plainpass"
