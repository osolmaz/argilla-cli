"""Tests for the API-key-first login: both methods, and the key winning."""

import json
import sys
import types

import httpx

from argilla_cli import auth
from argilla_cli.cli import main

URL = "https://studio.example.org"
KEY = "apikey-secret-xyz"
PASSWORD = "sesame"
USER = {
    "id": "11111111-1111-1111-1111-111111111111",
    "username": "amelie",
    "first_name": "Amelie",
    "last_name": "Laurent",
    "role": "admin",
    "api_key": "in-memory-key-from-me",
}


def key_handler(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/api/v1/me":
        assert request.headers["X-Argilla-Api-Key"] == KEY
        return httpx.Response(200, json=USER)
    return httpx.Response(404)


def password_handler(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/api/v1/token":
        return httpx.Response(200, json={"access_token": "token-secret-abc"})
    if request.url.path == "/api/v1/me":
        return httpx.Response(200, json=USER)
    return httpx.Response(404)


class TestCredentialsMethod:
    def test_api_key_wins_over_password(self):
        creds = auth.Credentials(url=URL, password=PASSWORD, api_key=KEY)
        assert creds.method == "api_key"

    def test_password_when_no_key(self):
        creds = auth.Credentials(url=URL, password=PASSWORD)
        assert creds.method == "password"

    def test_none_when_neither(self):
        creds = auth.Credentials(url=URL)
        assert creds.method is None


class TestLoginWithApiKey:
    def test_login_with_key_needs_no_username(self, runner, isolated_config, mock_http):
        mock_http(key_handler)
        result = runner.invoke(
            main,
            ["--json", "login", "--url", URL, "--api-key", KEY, "--account", "studio"],
        )
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["method"] == "api_key"
        assert payload["api_key"] == "***"
        assert payload["username"] == "amelie"  # from GET /api/v1/me, for display
        cfg = json.loads(isolated_config.read_text())
        entry = cfg["accounts"]["studio"]
        assert entry["api_key"] == KEY
        assert "password" not in entry

    def test_login_prompt_asks_for_the_key_without_username(self, runner, isolated_config, mock_http, monkeypatch):
        from argilla_cli.commands import account as account_mod

        monkeypatch.setattr(account_mod, "has_terminal_stdin", lambda: True)
        mock_http(key_handler)
        result = runner.invoke(
            main,
            ["--json", "login", "--url", URL, "--account", "studio"],
            input=f"{KEY}\n",
        )
        assert result.exit_code == 0, result.output
        assert "API key for" in result.output
        assert KEY not in result.output
        cfg = json.loads(isolated_config.read_text())
        assert cfg["accounts"]["studio"]["api_key"] == KEY

    def test_key_only_account_never_hits_the_token_endpoint(self, runner, isolated_config, mock_http):
        def handler(request):
            raise AssertionError(f"unexpected request to {request.url.path}")

        def first_call_only(request):
            if request.url.path == "/api/v1/me":
                return key_handler(request)
            return handler(request)

        mock_http(first_call_only)
        result = runner.invoke(
            main,
            ["--json", "login", "--url", URL, "--api-key", KEY, "--account", "studio"],
        )
        assert result.exit_code == 0, result.output

    def test_mismatched_username_is_not_checked_without_a_username(self, runner, isolated_config, mock_http):
        mock_http(key_handler)
        result = runner.invoke(
            main,
            ["--json", "login", "--url", URL, "--api-key", KEY, "--account", "studio"],
        )
        assert result.exit_code == 0, result.output


class TestApiKeyWins:
    def test_flag_level_key_beats_flag_password(self, runner, isolated_config, mock_http):
        mock_http(key_handler)
        result = runner.invoke(
            main,
            ["--json", "login", "--url", URL, "--api-key", KEY, "--password", PASSWORD, "--account", "studio"],
        )
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["method"] == "api_key"
        # The unused password is not saved.
        cfg = json.loads(isolated_config.read_text())
        assert "password" not in cfg["accounts"]["studio"]

    def test_env_level_key_beats_env_password(self, isolated_config, mock_http, monkeypatch):
        monkeypatch.setenv("ARGILLA_API_KEY", KEY)
        monkeypatch.setenv("ARGILLA_PASSWORD", PASSWORD)
        mock_http(key_handler)
        creds = auth.resolve_credentials()
        assert creds.method == "api_key"

    def test_file_level_key_beats_file_password(self, isolated_config, mock_http, tmp_path):
        f = tmp_path / "creds.env"
        f.write_text(f"API_KEY={KEY}\nPASSWORD={PASSWORD}\n")
        creds = auth.resolve_credentials(FakeCtx(obj={"credentials_file": str(f)}))
        assert creds.method == "api_key"

    def test_explicit_key_flag_beats_env_password(self, isolated_config, mock_http, monkeypatch):
        monkeypatch.setenv("ARGILLA_PASSWORD", PASSWORD)
        mock_http(key_handler)
        creds = auth.resolve_credentials(FakeCtx(obj={"api_key": KEY}))
        assert creds.method == "api_key"


class FakeCtx:
    def __init__(self, obj=None, params=None):
        self.obj = obj or {}
        self.params = params or {}


class TestWhoamiAndDoctorReportMethod:
    def test_whoami_says_api_key(self, runner, isolated_config, mock_http):
        mock_http(key_handler)
        result = runner.invoke(main, ["--json", "--api-key", KEY, "--url", URL, "whoami"])
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["method"] == "api_key"
        assert KEY not in result.output

    def test_whoami_says_password(self, runner, isolated_config, mock_http):
        mock_http(password_handler)
        result = runner.invoke(
            main,
            ["--json", "--url", URL, "--username", "amelie", "--password", PASSWORD, "whoami"],
        )
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["method"] == "password"
        assert PASSWORD not in result.output

    def test_doctor_login_check_names_the_method(self, runner, isolated_config, mock_http):
        def handler(request):
            if request.url.path == "/api/v1/version":
                return httpx.Response(200, json={"version": "2.8.0"})
            return key_handler(request)

        mock_http(handler)
        result = runner.invoke(main, ["--json", "--api-key", KEY, "--url", URL, "doctor"])
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output.split("\nError:")[0])
        login_check = next(c for c in payload["checks"] if c["name"] == "login")
        assert "(api_key)" in login_check["detail"]
        assert KEY not in result.output


class TestBuildClient:
    def test_api_key_passes_straight_to_the_sdk(self, monkeypatch):
        captured = _fake_argilla(monkeypatch)
        creds = auth.Credentials(url=URL, api_key=KEY)
        auth.build_client(creds)
        assert captured == {"api_url": URL, "api_key": KEY}

    def test_password_path_exchanges_then_uses_the_me_api_key(self, mock_http, monkeypatch):
        captured = _fake_argilla(monkeypatch)
        mock_http(password_handler)
        creds = auth.Credentials(url=URL, username="amelie", password=PASSWORD)
        auth.build_client(creds)
        # The key read from /me is what reaches the SDK, in memory only.
        assert captured == {"api_url": URL, "api_key": "in-memory-key-from-me"}


def _fake_argilla(monkeypatch) -> dict:
    captured: dict = {}

    class FakeArgilla:
        def __init__(self, api_url=None, api_key=None, **kwargs):
            captured["api_url"] = api_url
            captured["api_key"] = api_key

    fake = types.ModuleType("argilla")
    fake.Argilla = FakeArgilla  # type: ignore[attr-defined] -- test double
    monkeypatch.setitem(sys.modules, "argilla", fake)
    return captured


class TestMaskingBothMethods:
    def test_config_show_masks_api_key_entries(self, runner, isolated_config):
        isolated_config.parent.mkdir(parents=True, exist_ok=True)
        isolated_config.write_text(
            json.dumps(
                {
                    "active_account": "keyed",
                    "accounts": {"keyed": {"url": URL, "username": "amelie", "api_key": KEY}},
                }
            )
        )
        result = runner.invoke(main, ["config", "show"])
        assert result.exit_code == 0, result.output
        assert KEY not in result.output
        assert "***" in result.output

    def test_api_key_is_redacted_from_errors(self, runner, isolated_config, mock_http):
        def handler(request):
            raise httpx.ConnectError(f"refused while carrying {KEY}")

        mock_http(handler)
        result = runner.invoke(main, ["whoami", "--url", URL, "--api-key", KEY])
        assert result.exit_code != 0
        assert KEY not in result.output
        assert KEY not in (result.stderr or "")
