"""Shared test fixtures."""

import httpx
import pytest

from argilla_cli import masking


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Fail loudly if a test tries a real HTTP request.

    Modeled on discli's autouse no_network fixture. Patching at the HTTP layer
    catches a test that drives the CLI end to end and falls back to the
    developer's own ~/.argilla-cli/config.json, which would make live calls
    that pass locally and fail in CI.

    httpx.MockTransport clients still go through, so a unit test may exercise
    request parsing against a mock transport; a mock transport never touches
    the network.
    """
    real_send = httpx.Client.send
    real_async_send = httpx.AsyncClient.send

    def guarded_send(self, request, **kwargs):
        if isinstance(self._transport, httpx.MockTransport):
            return real_send(self, request, **kwargs)
        raise AssertionError(
            "test attempted a real HTTP request; patch argilla_cli.auth helpers "
            "or use httpx.MockTransport instead"
        )

    async def guarded_async_send(self, request, **kwargs):
        if isinstance(self._transport, httpx.MockTransport):
            return await real_async_send(self, request, **kwargs)
        raise AssertionError(
            "test attempted a real HTTP request; patch argilla_cli.auth helpers "
            "or use httpx.MockTransport instead"
        )

    monkeypatch.setattr(httpx.Client, "send", guarded_send)
    monkeypatch.setattr(httpx.AsyncClient, "send", guarded_async_send)


@pytest.fixture(autouse=True)
def clean_secrets():
    """Keep registered secrets from leaking between tests."""
    masking.reset_registered()
    yield
    masking.reset_registered()


@pytest.fixture(autouse=True)
def isolated_config(monkeypatch, tmp_path):
    """Point the config at a temp file so tests never touch the real one."""
    config_path = tmp_path / "argilla-cli" / "config.json"
    monkeypatch.setenv("ARGILLA_CLI_CONFIG", str(config_path))
    return config_path


@pytest.fixture()
def runner():
    from click.testing import CliRunner
    return CliRunner()


@pytest.fixture()
def mock_http(monkeypatch):
    """Route argilla_cli.auth raw HTTP calls through a MockTransport.

    Set the handler with ``mock_http(handler)``. The no_network fixture above
    allows MockTransport clients and blocks everything else, so a test can
    exercise request parsing but never reach a real server.
    """
    state = {"handler": lambda request: httpx.Response(404, json={"detail": "no handler set"})}

    def set_handler(handler):
        state["handler"] = handler

    def fake_http_client():
        return httpx.Client(transport=httpx.MockTransport(state["handler"]))

    from argilla_cli import auth
    monkeypatch.setattr(auth, "http_client", fake_http_client)
    return set_handler
