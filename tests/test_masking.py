"""Tests for secret masking and redaction."""

import json

import httpx

from argilla_cli import masking
from argilla_cli.cli import main


def test_register_and_redact():
    masking.register_secrets(["sesame", "apikey-xyz"])
    assert masking.redact("the password is sesame and the key is apikey-xyz") == (
        "the password is *** and the key is ***"
    )


def test_register_ignores_empty_and_duplicates():
    masking.register_secrets(["", "sesame"])
    masking.register_secrets(["sesame"])
    assert masking.registered_secrets() == ["sesame"]


def test_mask_mapping():
    masked = masking.mask_mapping(
        {
            "password": "sesame",
            "api_key": "key-123",
            "url": "https://x.example.org",
            "empty_password": "",
            "name": "amelie",
        }
    )
    assert masked == {
        "password": "***",
        "api_key": "***",
        "url": "https://x.example.org",
        "empty_password": "",
        "name": "amelie",
    }


def test_redact_message_scrubs_url_queries():
    masking.register_secrets(["sesame"])
    text = masking.redact_message("GET https://studio.example.org/api?token=sesame&r=1 failed")
    assert "sesame" not in text
    assert "?<redacted>" in text


def test_config_show_masks_secrets(runner, isolated_config):
    isolated_config.parent.mkdir(parents=True, exist_ok=True)
    isolated_config.write_text(
        json.dumps(
            {
                "active_account": "studio",
                "accounts": {
                    "studio": {
                        "url": "https://studio.example.org",
                        "username": "amelie",
                        "password": "sesame-super-secret",
                        "api_key": "apikey-super-secret",
                    }
                },
            }
        )
    )
    result = runner.invoke(main, ["config", "show"])
    assert result.exit_code == 0, result.output
    assert "sesame-super-secret" not in result.output
    assert "apikey-super-secret" not in result.output
    assert "amelie" in result.output


def test_config_show_json_masks_secrets(runner, isolated_config):
    isolated_config.parent.mkdir(parents=True, exist_ok=True)
    isolated_config.write_text(
        json.dumps(
            {
                "active_account": "studio",
                "accounts": {
                    "studio": {
                        "url": "https://studio.example.org",
                        "username": "amelie",
                        "password": "sesame-super-secret",
                    }
                },
            }
        )
    )
    result = runner.invoke(main, ["--json", "config", "show"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["accounts"]["studio"]["password"] == "***"
    assert "sesame-super-secret" not in result.output


def test_cli_errors_are_redacted(runner, isolated_config, mock_http):
    def handler(request):
        raise httpx.ConnectError("connection refused for sesame-super-secret")

    mock_http(handler)
    result = runner.invoke(
        main,
        ["login", "--url", "https://studio.example.org", "--username", "amelie", "--password", "sesame-super-secret"],
    )
    assert result.exit_code != 0
    combined = result.output + (result.stderr or "")
    assert "sesame-super-secret" not in combined
