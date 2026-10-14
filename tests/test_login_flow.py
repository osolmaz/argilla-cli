"""Tests for the login flow: token exchange, /me, and the login command."""

import json

import httpx
import pytest

from argilla_cli import auth
from argilla_cli.cli import main

URL = "https://studio.example.org"
USER = {
    "id": "11111111-1111-1111-1111-111111111111",
    "username": "amelie",
    "first_name": "Amelie",
    "last_name": "Laurent",
    "role": "admin",
    "api_key": "apikey-secret-xyz",
}


def token_ok_handler(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/api/v1/token":
        body = request.content.decode()
        assert "username=amelie" in body
        assert "password=" in body
        return httpx.Response(200, json={"access_token": "token-secret-abc", "token_type": "bearer"})
    if request.url.path == "/api/v1/me":
        assert request.headers["Authorization"] == "Bearer token-secret-abc"
        return httpx.Response(200, json=USER)
    return httpx.Response(404)


class TestRequestToken:
    def test_returns_token_on_200(self, mock_http):
        mock_http(token_ok_handler)
        token = auth.request_token(URL, "amelie", "sesame")
        assert token == "token-secret-abc"

    def test_token_request_carries_form_fields(self, mock_http):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["content_type"] = request.headers.get("content-type", "")
            seen["body"] = request.content.decode()
            return httpx.Response(200, json={"access_token": "t"})

        mock_http(handler)
        auth.request_token(URL, "amelie", "sesame")
        assert "application/x-www-form-urlencoded" in seen["content_type"]
        assert "username=amelie" in seen["body"]
        assert "password=sesame" in seen["body"]

    def test_401_is_a_clean_error(self, mock_http):
        mock_http(lambda request: httpx.Response(401, json={"detail": "bad"}))
        with pytest.raises(auth.CredentialsError, match="Invalid username or password"):
            auth.request_token(URL, "amelie", "wrong")

    def test_other_status_includes_it(self, mock_http):
        mock_http(lambda request: httpx.Response(500, text="boom"))
        with pytest.raises(auth.CredentialsError, match="500"):
            auth.request_token(URL, "amelie", "sesame")

    def test_connection_error_names_the_server(self, mock_http):
        def handler(request):
            raise httpx.ConnectError("nope")

        mock_http(handler)
        with pytest.raises(auth.CredentialsError, match="Could not reach"):
            auth.request_token(URL, "amelie", "sesame")


class TestLoginWithPassword:
    def test_returns_user_and_registers_secrets(self, mock_http):
        mock_http(token_ok_handler)
        user = auth.login_with_password(URL, "amelie", "sesame")
        assert user["username"] == "amelie"
        assert user["role"] == "admin"
        # Token and API key are registered for redaction, and stay in memory.
        registered = auth.masking.registered_secrets()
        assert "token-secret-abc" in registered
        assert USER["api_key"] in registered

    def test_known_secrets_stay_out_of_returned_errors(self, mock_http):
        def handler(request):
            if request.url.path == "/api/v1/token":
                return httpx.Response(200, json={"access_token": "token-secret-abc"})
            return httpx.Response(403, text="denied for user with password sesame")

        mock_http(handler)
        with pytest.raises(auth.CredentialsError) as exc_info:
            auth.login_with_password(URL, "amelie", "sesame")
        # The password was registered when login started, and the error
        # message never quotes a response body: a server echo of a secret
        # cannot survive into the error message at all.
        message = str(exc_info.value)
        assert "sesame" not in message
        assert "denied for user" not in message
        assert "HTTP 403" in message


class TestLoginWithApiKey:
    def test_sends_the_api_key_header(self, mock_http):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["header"] = request.headers.get("X-Argilla-Api-Key")
            return httpx.Response(200, json=USER)

        mock_http(handler)
        user = auth.login_with_api_key(URL, "apikey-secret-xyz")
        assert user["username"] == "amelie"
        assert seen["header"] == "apikey-secret-xyz"


class TestLoginCommand:
    def test_login_saves_account_and_activates_it(self, runner, isolated_config, mock_http):
        mock_http(token_ok_handler)
        result = runner.invoke(
            main,
            ["--json", "login", "--url", URL, "--username", "amelie", "--password", "sesame", "--account", "studio"],
        )
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["account"] == "studio"
        assert payload["password"] == "***"
        assert payload["api_key"] is None
        cfg = json.loads(isolated_config.read_text())
        assert cfg["active_account"] == "studio"
        assert cfg["accounts"]["studio"]["password"] == "sesame"
        # The API key read from /me stays in memory: it is not saved.
        assert "api_key" not in cfg["accounts"]["studio"]

    def test_login_with_api_key_saves_it(self, runner, isolated_config, mock_http):
        def handler(request):
            if request.url.path == "/api/v1/me":
                assert request.headers["X-Argilla-Api-Key"] == "apikey-secret-xyz"
                return httpx.Response(200, json=USER)
            return httpx.Response(404)

        mock_http(handler)
        result = runner.invoke(
            main,
            [
                "--json",
                "login",
                "--url",
                URL,
                "--username",
                "amelie",
                "--api-key",
                "apikey-secret-xyz",
                "--account",
                "keyed",
            ],
        )
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["api_key"] == "***"
        cfg = json.loads(isolated_config.read_text())
        assert cfg["accounts"]["keyed"]["api_key"] == "apikey-secret-xyz"
        assert "password" not in cfg["accounts"]["keyed"]

    def test_login_credentials_file_stores_path_not_secret(self, runner, isolated_config, mock_http, tmp_path):
        f = tmp_path / "creds.env"
        f.write_text("USERNAME=amelie\nPASSWORD=filepass\n")
        mock_http(token_ok_handler)
        result = runner.invoke(
            main,
            [
                "--json",
                "login",
                "--url",
                URL,
                "--username",
                "amelie",
                "--credentials-file",
                str(f),
                "--account",
                "filed",
            ],
        )
        assert result.exit_code == 0, result.output
        cfg = json.loads(isolated_config.read_text())
        entry = cfg["accounts"]["filed"]
        assert entry["credentials_file"] == str(f)
        assert "password" not in entry
        assert "filepass" not in isolated_config.read_text()

    def test_login_prompts_for_password_when_missing(self, runner, isolated_config, mock_http, monkeypatch):
        from argilla_cli.commands import account as account_mod

        monkeypatch.setattr(account_mod, "has_terminal_stdin", lambda: True)
        mock_http(token_ok_handler)
        result = runner.invoke(
            main,
            ["--json", "login", "--url", URL, "--username", "amelie", "--account", "studio"],
            input="sesame\n",
        )
        assert result.exit_code == 0, result.output
        cfg = json.loads(isolated_config.read_text())
        assert cfg["accounts"]["studio"]["password"] == "sesame"

    def test_login_without_terminal_and_without_password_fails(self, runner, isolated_config, mock_http):
        mock_http(token_ok_handler)
        result = runner.invoke(
            main,
            ["login", "--url", URL, "--username", "amelie"],
        )
        assert result.exit_code != 0
        assert "No password or API key" in result.stderr

    def test_login_rejects_mismatched_username(self, runner, isolated_config, mock_http):
        def handler(request):
            if request.url.path == "/api/v1/token":
                return httpx.Response(200, json={"access_token": "t"})
            return httpx.Response(200, json={**USER, "username": "someone-else"})

        mock_http(handler)
        result = runner.invoke(
            main,
            ["login", "--url", URL, "--username", "amelie", "--password", "sesame"],
        )
        assert result.exit_code != 0
        assert "someone-else" in result.stderr

    def test_login_bad_password_is_a_clean_error(self, runner, isolated_config, mock_http):
        mock_http(lambda request: httpx.Response(401, json={"detail": "bad"}))
        result = runner.invoke(
            main,
            ["login", "--url", URL, "--username", "amelie", "--password", "wrong"],
        )
        assert result.exit_code != 0
        assert "Invalid username or password" in result.stderr

    def test_login_default_account_name_is_the_host(self, runner, isolated_config, mock_http):
        mock_http(token_ok_handler)
        result = runner.invoke(
            main,
            ["--json", "login", "--url", URL, "--username", "amelie", "--password", "sesame"],
        )
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["account"] == "studio.example.org"

    def test_login_output_never_carries_the_secret(self, runner, isolated_config, mock_http):
        mock_http(token_ok_handler)
        result = runner.invoke(
            main,
            ["--json", "login", "--url", URL, "--username", "amelie", "--password", "sesame-super-secret"],
        )
        assert result.exit_code == 0, result.output
        assert "sesame-super-secret" not in result.output
        assert "sesame-super-secret" not in (result.stderr or "")
