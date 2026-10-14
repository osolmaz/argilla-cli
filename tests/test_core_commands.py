"""Tests for whoami, info, doctor, setup, and the account commands."""

import json

import httpx
import pytest

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


def default_handler(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/api/v1/token":
        username = _form_username(request)
        return httpx.Response(200, json={"access_token": f"{username}-token"})
    if request.url.path == "/api/v1/me":
        # The token carries the username, so several accounts can log in
        # against the same mock.
        token = request.headers.get("Authorization", "").removeprefix("Bearer ")
        username = token.removesuffix("-token") or "amelie"
        return httpx.Response(200, json={**USER, "username": username})
    if request.url.path == "/api/v1/version":
        return httpx.Response(200, json={"version": "2.8.0"})
    return httpx.Response(404)


def _form_username(request: httpx.Request) -> str:
    body = request.content.decode()
    for pair in body.split("&"):
        key, _, value = pair.partition("=")
        if key == "username":
            return value
    return "amelie"


@pytest.fixture()
def logged_in(isolated_config, mock_http):
    """A saved account whose credentials file carries the password."""
    creds_file = isolated_config.parent / "studio.env"
    creds_file.parent.mkdir(parents=True, exist_ok=True)
    creds_file.write_text("USERNAME=amelie\nPASSWORD=filepass\n")
    isolated_config.write_text(json.dumps({
        "active_account": "studio",
        "accounts": {"studio": {
            "url": URL,
            "username": "amelie",
            "credentials_file": str(creds_file),
        }},
    }))
    mock_http(default_handler)
    return isolated_config


class TestVersion:
    def test_version_flag(self, runner):
        result = runner.invoke(main, ["--version"])
        assert result.exit_code == 0, result.output
        assert "argilla-cli" in result.output

    def test_help_lists_every_milestone_one_command(self, runner):
        result = runner.invoke(main, ["--help"])
        assert result.exit_code == 0, result.output
        for name in ("login", "whoami", "info", "doctor", "setup", "account", "config"):
            assert name in result.output


class TestWhoami:
    def test_whoami_json(self, runner, logged_in):
        result = runner.invoke(main, ["--json", "whoami"])
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["username"] == "amelie"
        assert payload["role"] == "admin"
        assert payload["url"] == URL
        # The API key is a secret and must never appear.
        assert "api_key" not in payload
        assert "apikey-secret-xyz" not in result.output

    def test_whoami_table(self, runner, logged_in):
        result = runner.invoke(main, ["whoami"])
        assert result.exit_code == 0, result.output
        assert "amelie" in result.output
        assert "apikey-secret-xyz" not in result.output

    def test_whoami_without_credentials_fails_cleanly(self, runner, isolated_config):
        result = runner.invoke(main, ["whoami"])
        assert result.exit_code != 0
        assert "No server URL" in result.stderr


class TestInfo:
    def test_info_json(self, runner, isolated_config, mock_http, monkeypatch):
        mock_http(lambda request: httpx.Response(200, json={"version": "2.8.0"}))
        result = runner.invoke(main, ["--json", "--url", URL, "info"])
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["server_version"] == "2.8.0"
        assert payload["sdk_version"].startswith("2.8.0")
        assert payload["config_path"].endswith("config.json")

    def test_info_without_server_still_reports(self, runner, isolated_config):
        result = runner.invoke(main, ["--json", "info"])
        assert result.exit_code == 0, result.output
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["server_version"] is None


class TestDoctor:
    def test_all_checks_pass(self, runner, logged_in):
        result = runner.invoke(main, ["--json", "doctor"])
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["ok"] is True
        statuses = {c["name"]: c["status"] for c in payload["checks"]}
        assert statuses == {"url": "ok", "login": "ok", "version": "ok", "role": "ok"}
        assert payload["checks"][3]["detail"] == "admin"

    def test_version_mismatch_warns_but_ok(self, runner, logged_in, mock_http):
        def handler(request):
            if request.url.path == "/api/v1/version":
                return httpx.Response(200, json={"version": "2.3.0"})
            return default_handler(request)

        mock_http(handler)
        result = runner.invoke(main, ["--json", "doctor"])
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["ok"] is True
        statuses = {c["name"]: c["status"] for c in payload["checks"]}
        assert statuses["version"] == "warn"

    def test_unreachable_url_fails(self, runner, isolated_config, mock_http):
        def handler(request):
            raise httpx.ConnectError("nope")

        mock_http(handler)
        result = runner.invoke(main, ["--json", "--url", URL, "doctor"])
        assert result.exit_code != 0
        payload = json.loads(result.output.split("\nError:")[0])
        assert payload["ok"] is False

    def test_server_version_plain_text_is_accepted(self, runner, logged_in, mock_http):
        def handler(request):
            if request.url.path == "/api/v1/version":
                return httpx.Response(200, text="2.8.0.dev0\n")
            return default_handler(request)

        mock_http(handler)
        result = runner.invoke(main, ["--json", "doctor"])
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        statuses = {c["name"]: c["status"] for c in payload["checks"]}
        # 2.8.0.dev0 and the installed 2.8.0 SDK are the same release.
        assert statuses["version"] == "ok"

    def test_table_output_and_failure_exit(self, runner, isolated_config, mock_http):
        def handler(request):
            raise httpx.ConnectError("nope")

        mock_http(handler)
        result = runner.invoke(main, ["--url", URL, "doctor"])
        assert result.exit_code != 0
        assert "FAIL" in result.output


class TestSetup:
    def test_setup_refuses_without_terminal(self, runner, isolated_config):
        result = runner.invoke(main, ["setup"])
        assert result.exit_code != 0
        assert "needs a terminal" in result.stderr

    def test_setup_wizard_saves_account(self, runner, isolated_config, mock_http, monkeypatch):
        from argilla_cli.commands import account as account_mod

        monkeypatch.setattr(account_mod, "has_terminal_stdin", lambda: True)
        mock_http(default_handler)
        result = runner.invoke(
            main,
            ["--json", "setup"],
            input="https://studio.example.org\namelie\nsesame\nstudio\nfull\n",
        )
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output[result.output.index("{"):])
        assert payload["account"] == "studio"
        assert payload["password"] == "***"
        assert "sesame" not in result.output
        cfg = json.loads(isolated_config.read_text())
        assert cfg["accounts"]["studio"]["password"] == "sesame"


class TestAccountCommands:
    def test_account_list_use_remove(self, runner, isolated_config, mock_http):
        mock_http(default_handler)
        first = runner.invoke(main, ["--json", "login", "--url", URL, "--username", "amelie",
                                     "--password", "sesame", "--account", "studio"])
        assert first.exit_code == 0, first.output
        second = runner.invoke(main, ["--json", "login", "--url", "https://other.example.org", "--username", "bob",
                                      "--password", "hunter2", "--account", "other"])
        assert second.exit_code == 0, second.output

        result = runner.invoke(main, ["--json", "account", "list"])
        assert result.exit_code == 0, result.output
        accounts = json.loads(result.output)
        by_name = {a["name"]: a for a in accounts}
        assert set(by_name) == {"studio", "other"}
        # The last login is the active account.
        assert by_name["other"]["active"] is True
        assert by_name["other"]["password"] == "***"
        assert "sesame" not in result.output and "hunter2" not in result.output

        result = runner.invoke(main, ["--json", "account", "use", "other"])
        assert result.exit_code == 0, result.output
        assert json.loads(result.output)["active_account"] == "other"

        result = runner.invoke(main, ["--json", "account", "remove", "--yes", "other"])
        assert result.exit_code == 0, result.output
        assert json.loads(result.output)["removed"] == "other"

        result = runner.invoke(main, ["--json", "account", "list"])
        assert [a["name"] for a in json.loads(result.output)] == ["studio"]

    def test_account_remove_needs_yes_without_terminal(self, runner, isolated_config):
        runner.invoke(main, ["--json", "login", "--url", URL, "--username", "amelie",
                             "--password", "sesame", "--account", "studio"])
        result = runner.invoke(main, ["account", "remove", "studio"])
        assert result.exit_code != 0
        assert "--yes" in result.stderr

    def test_account_use_unknown_fails(self, runner, isolated_config):
        result = runner.invoke(main, ["account", "use", "ghost"])
        assert result.exit_code != 0
        assert "No account named" in result.stderr
