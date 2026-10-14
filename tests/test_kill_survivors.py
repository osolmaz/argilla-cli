"""Surgical unit tests for doctor checks, config writes, and output shapes.

These pin exact messages, request shapes, and file modes so the mutation gate
has something real to bite on.
"""

import json
import os
from unittest.mock import MagicMock

import httpx
import pytest

from argilla_cli import auth
from argilla_cli import config as config_mod
from argilla_cli import output as output_mod
from argilla_cli.cli import _force_utf8_output
from argilla_cli.commands import core as core_mod
from argilla_cli.commands.helpers import get_client, get_credentials, quiet_call
from argilla_cli.errors import ArgillaCLIError
from argilla_cli.profiles import confirm_destructive

URL = "https://lab.example.org"
KEY = "k" * 8
PASSWORD = "p" * 8


def make_creds(**overrides):
    return auth.Credentials(
        url=overrides.get("url", URL),
        username=overrides.get("username"),
        password=overrides.get("password"),
        api_key=overrides.get("api_key"),
    )


class TestCheckVersion:
    def test_none_version_warns(self):
        check = core_mod._check_version(None)
        assert check == {"name": "version", "status": "warn", "detail": "No server version to compare."}

    def test_missing_sdk_fails(self, monkeypatch):
        monkeypatch.setattr(core_mod, "_sdk_version", lambda: "not installed")
        check = core_mod._check_version("2.8.0")
        assert check == {"name": "version", "status": "fail", "detail": "The argilla SDK is not installed."}

    def test_matching_release_is_ok(self, monkeypatch):
        monkeypatch.setattr(core_mod, "_sdk_version", lambda: "2.8.0")
        check = core_mod._check_version("2.8.0.dev0")
        assert check["status"] == "ok"
        assert check["detail"] == "Server 2.8.0.dev0, SDK 2.8.0."

    def test_differing_release_warns(self, monkeypatch):
        monkeypatch.setattr(core_mod, "_sdk_version", lambda: "2.9.1")
        check = core_mod._check_version("2.8.0")
        assert check["status"] == "warn"
        assert check["detail"].startswith("Server 2.8.0 and SDK 2.9.1 differ.")

    @pytest.mark.parametrize(
        ("raw", "release"),
        [
            ("2.8.0.dev0", "2.8.0"),
            ("2.8.0+local", "2.8.0"),
            ("2.8.0rc1", "2.8.0"),
            ("2.8.0a1", "2.8.0"),
            ("2.8.0b2", "2.8.0"),
            ("2.8.0", "2.8.0"),
        ],
    )
    def test_release_strips_suffixes(self, raw, release):
        assert core_mod._release(raw) == release


class TestCheckLogin:
    def test_no_credentials_fails_with_advice(self):
        check, user = core_mod._check_login(make_creds(url=None))
        assert check["status"] == "fail"
        assert check["detail"] == "No credentials to try. Run `argilla-cli login`."
        assert user == {}

    def test_failing_login_is_redacted_and_empty(self, monkeypatch):
        def boom(creds):
            raise auth.CredentialsError("Invalid username or password for https://lab.example.org.")

        monkeypatch.setattr(core_mod, "_authenticated_user", boom)
        check, user = core_mod._check_login(make_creds(api_key=KEY))
        assert check["status"] == "fail"
        assert "Invalid username or password" in check["detail"]
        assert user == {}

    def test_api_key_login_reports_method(self, monkeypatch):
        monkeypatch.setattr(core_mod, "_authenticated_user", lambda creds: {"username": "amelie"})
        check, user = core_mod._check_login(make_creds(api_key=KEY))
        assert check["status"] == "ok"
        assert check["detail"] == "Logged in as amelie (api_key)."
        assert user == {"username": "amelie"}

    def test_password_login_reports_method(self, monkeypatch):
        monkeypatch.setattr(core_mod, "_authenticated_user", lambda creds: {"username": "amelie"})
        check, _ = core_mod._check_login(make_creds(username="amelie", password=PASSWORD))
        assert check["detail"] == "Logged in as amelie (password)."


class TestCheckUrl:
    def test_no_url_fails_with_advice(self):
        check = core_mod._check_url(make_creds(url=None))
        assert check == {
            "name": "url",
            "status": "fail",
            "detail": "No server URL. Run `argilla-cli login` or pass --url.",
        }

    def test_unreachable_fails_with_endpoint(self, monkeypatch):
        monkeypatch.setattr(core_mod.auth, "server_version", lambda url: None)
        check = core_mod._check_url(make_creds())
        assert check == {
            "name": "url",
            "status": "fail",
            "detail": f"Could not reach {URL} (GET /api/v1/version).",
        }

    def test_reachable_is_ok(self, monkeypatch):
        monkeypatch.setattr(core_mod.auth, "server_version", lambda url: "2.8.0.dev0")
        check = core_mod._check_url(make_creds())
        assert check == {"name": "url", "status": "ok", "detail": f"Reachable at {URL}."}


class TestAuthenticatedUser:
    def test_prefers_api_key(self, monkeypatch):
        seen = {}

        def fake_api_key_login(url, api_key):
            seen["args"] = (url, api_key)
            return {"username": "amelie"}

        monkeypatch.setattr(core_mod.auth, "login_with_api_key", fake_api_key_login)
        monkeypatch.setattr(
            core_mod.auth,
            "login_with_password",
            lambda *args: pytest.fail("password login must not run when an API key is set"),
        )
        user = core_mod._authenticated_user(make_creds(username="amelie", password=PASSWORD, api_key=KEY))
        assert user == {"username": "amelie"}
        assert seen["args"] == (URL, KEY)

    def test_username_without_password_errors(self):
        with pytest.raises(auth.CredentialsError) as excinfo:
            core_mod._authenticated_user(make_creds(username="amelie"))
        assert "No credentials for this account" in excinfo.value.message


class TestForceUtf8Output:
    class FakeStream:
        def __init__(self, fail=False):
            self.calls = []
            self._fail = fail

        def reconfigure(self, **kwargs):
            self.calls.append(kwargs)
            if self._fail:
                raise OSError("cannot reconfigure")

    def test_reconfigures_both_streams(self, monkeypatch):
        out, err = self.FakeStream(), self.FakeStream()
        monkeypatch.setattr("sys.stdout", out)
        monkeypatch.setattr("sys.stderr", err)
        _force_utf8_output()
        assert out.calls == [{"encoding": "utf-8", "errors": "replace"}]
        assert err.calls == [{"encoding": "utf-8", "errors": "replace"}]

    def test_survives_streams_that_cannot_reconfigure(self, monkeypatch):
        class NoReconfigure:
            pass

        monkeypatch.setattr("sys.stdout", NoReconfigure())
        err = self.FakeStream(fail=True)
        monkeypatch.setattr("sys.stderr", err)
        _force_utf8_output()  # must not raise
        assert err.calls


class TestSaveConfig:
    def test_tightens_a_loose_existing_file(self, isolated_config):
        isolated_config.parent.mkdir(parents=True)
        isolated_config.write_text("{}")
        os.chmod(isolated_config, 0o644)
        config_mod.save_config({"accounts": {}}, isolated_config)
        assert os.stat(isolated_config).st_mode & 0o777 == 0o600
        assert json.loads(isolated_config.read_text()) == {"accounts": {}}

    def test_directory_mode(self, tmp_path):
        target = tmp_path / "deep" / "dir" / "config.json"
        config_mod.save_config({"a": 1}, target)
        assert os.stat(target.parent).st_mode & 0o777 == 0o700
        assert json.loads(target.read_text()) == {"a": 1}


class TestConfirmDestructive:
    def test_ignores_non_destructive_commands(self):
        confirm_destructive("datasets list", yes=False)  # must not raise

    def test_yes_skips_the_prompt(self):
        confirm_destructive("users delete", yes=True, details="bob")  # must not raise

    def test_non_terminal_needs_yes(self, monkeypatch):
        monkeypatch.setattr("sys.stdin", MagicMock(isatty=lambda: False))
        with pytest.raises(ArgillaCLIError) as excinfo:
            confirm_destructive("users delete", yes=False, details="bob")
        assert "no terminal to confirm it" in excinfo.value.message
        assert excinfo.value.message.endswith("Re-run with --yes.")

    def test_terminal_prompt_names_the_target(self, monkeypatch):
        monkeypatch.setattr("sys.stdin", MagicMock(isatty=lambda: True))
        asked = {}

        def fake_confirm(text, **kwargs):
            asked["text"] = text
            raise click_abort()

        from click.exceptions import Abort

        def click_abort():
            return Abort()

        monkeypatch.setattr(output_mod.click, "confirm", fake_confirm)
        with pytest.raises(Abort):
            confirm_destructive("users delete", yes=False, details="bob")
        assert asked["text"] == "Destructive action: users delete (bob). Continue?"


class TestGetClientCaching:
    def test_builds_the_client_once(self, monkeypatch, isolated_config):
        config_mod.save_account("fake", {"url": URL, "api_key": KEY}, path=isolated_config)
        calls = []

        def fake_build(creds):
            calls.append(creds)
            return object()

        monkeypatch.setattr(auth, "build_client", fake_build)

        ctx = MagicMock()
        ctx.obj = {}
        first = get_client(ctx)
        second = get_client(ctx)
        assert first is second
        assert len(calls) == 1

    def test_credentials_cached_on_context(self, monkeypatch, isolated_config):
        config_mod.save_account("fake", {"url": URL, "api_key": KEY}, path=isolated_config)
        seen = []
        real = auth.resolve_credentials

        def counting(ctx, **kwargs):
            seen.append(1)
            return real(ctx, **kwargs)

        monkeypatch.setattr(auth, "resolve_credentials", counting)
        ctx = MagicMock()
        ctx.obj = {}
        get_credentials(ctx)
        get_credentials(ctx)
        assert len(seen) == 1

    def test_missing_url_names_the_remedy(self):
        ctx = MagicMock()
        ctx.obj = {}
        with pytest.raises(auth.CredentialsError) as excinfo:
            get_client(ctx)
        assert excinfo.value.message == "No server URL. Run `argilla-cli login` or pass --url."


class TestQuietCall:
    def test_hides_the_sdk_warning_but_keeps_the_value(self):
        import warnings

        def noisy():
            warnings.warn("Workspace not found", stacklevel=2)
            return 42

        assert quiet_call(noisy) == 42

    def test_propagates_errors(self):
        def broken():
            raise ValueError("nope")

        with pytest.raises(ValueError, match="nope"):
            quiet_call(broken)


class TestRequestShapes:
    def test_request_me_uses_the_api_key_header(self, mock_http):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["headers"] = dict(request.headers)
            return httpx.Response(200, json={"username": "amelie"})

        mock_http(handler)
        user = auth.request_me(URL, api_key=KEY)
        assert user["username"] == "amelie"
        assert seen["url"] == f"{URL}/api/v1/me"
        assert seen["headers"]["x-argilla-api-key"] == KEY
        assert "authorization" not in seen["headers"]

    def test_request_me_uses_the_bearer_header(self, mock_http):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["headers"] = dict(request.headers)
            return httpx.Response(200, json={"username": "amelie"})

        mock_http(handler)
        auth.request_me(URL, token="tok-123")
        assert seen["headers"]["authorization"] == "Bearer tok-123"
        assert "x-argilla-api-key" not in seen["headers"]

    def test_request_me_without_either_fails(self):
        with pytest.raises(auth.CredentialsError) as excinfo:
            auth.request_me(URL)
        assert excinfo.value.message == "Checking the user needs a token or an API key."

    def test_request_token_posts_form_fields(self, mock_http):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["body"] = request.content.decode()
            seen["content_type"] = request.headers["content-type"]
            return httpx.Response(200, json={"access_token": "tok"})

        mock_http(handler)
        token = auth.request_token(URL, "amelie", PASSWORD)
        assert token == "tok"
        assert seen["url"] == f"{URL}/api/v1/token"
        assert seen["content_type"].startswith("application/x-www-form-urlencoded")
        assert "username=amelie" in seen["body"]

    def test_request_token_401_message(self, mock_http):
        mock_http(lambda request: httpx.Response(401, json={}))
        with pytest.raises(auth.CredentialsError) as excinfo:
            auth.request_token(URL, "amelie", "wrong")
        assert excinfo.value.message == f"Invalid username or password for {URL}."

    def test_server_version_accepts_a_bare_string(self, mock_http):
        mock_http(lambda request: httpx.Response(200, text="2.8.0.dev0\n"))
        assert auth.server_version(URL) == "2.8.0.dev0"

    def test_server_version_accepts_a_json_object(self, mock_http):
        mock_http(lambda request: httpx.Response(200, json={"version": "2.8.0"}))
        assert auth.server_version(URL) == "2.8.0"
