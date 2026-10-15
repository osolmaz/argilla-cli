"""Tests for the config file and named accounts."""

import os

import pytest

from argilla_cli import config as config_mod
from argilla_cli.errors import ConfigError


def test_load_missing_config_returns_empty(isolated_config):
    assert config_mod.load_config() == {}


def test_save_and_load_round_trip(isolated_config):
    config_mod.save_config({"active_account": "studio", "accounts": {}})
    assert config_mod.load_config() == {"active_account": "studio", "accounts": {}}


def test_saved_file_and_dir_permissions(isolated_config):
    config_mod.save_config({"accounts": {}})
    parent = isolated_config.parent
    assert (os.stat(parent).st_mode & 0o777) == 0o700
    assert (os.stat(isolated_config).st_mode & 0o777) == 0o600


def test_permissions_tightened_on_existing_loose_file(isolated_config):
    isolated_config.parent.mkdir(parents=True, exist_ok=True)
    isolated_config.write_text("{}")
    isolated_config.chmod(0o644)
    config_mod.save_config({"accounts": {}})
    assert (os.stat(isolated_config).st_mode & 0o777) == 0o600


def test_load_invalid_json_raises_clean_error(isolated_config):
    isolated_config.parent.mkdir(parents=True, exist_ok=True)
    isolated_config.write_text("not json{")
    with pytest.raises(ConfigError):
        config_mod.load_config()


def test_save_account_creates_and_activates(isolated_config):
    config_mod.save_account("studio", {"url": "https://studio.example.org", "username": "amelie"})
    cfg = config_mod.load_config()
    assert cfg["active_account"] == "studio"
    entry = config_mod.get_account(cfg, "studio")
    assert entry["url"] == "https://studio.example.org"
    assert entry["username"] == "amelie"
    assert entry["profile"] == "full"


def test_save_account_merges_and_keeps_other_accounts(isolated_config):
    config_mod.save_account("a", {"url": "https://a.example.org", "username": "u1"})
    config_mod.save_account("b", {"url": "https://b.example.org", "username": "u2"})
    config_mod.save_account("a", {"profile": "read-only"})
    cfg = config_mod.load_config()
    assert cfg["active_account"] == "a"
    assert config_mod.get_account(cfg, "b")["username"] == "u2"
    assert config_mod.get_account(cfg, "a")["profile"] == "read-only"
    assert config_mod.get_account(cfg, "a")["url"] == "https://a.example.org"


def test_save_account_rejects_bad_input(isolated_config):
    with pytest.raises(ConfigError):
        config_mod.save_account("with space", {"url": "https://x.example.org", "username": "u"})
    with pytest.raises(ConfigError):
        config_mod.save_account("ok", {"url": "ftp://x.example.org", "username": "u"})
    with pytest.raises(ConfigError):
        config_mod.save_account("ok", {"url": "https://x.example.org", "username": "u", "profile": "admin"})


def test_set_active_account(isolated_config):
    config_mod.save_account("a", {"url": "https://a.example.org", "username": "u1"})
    config_mod.save_account("b", {"url": "https://b.example.org", "username": "u2"})
    config_mod.set_active_account("a")
    assert config_mod.active_account_name(config_mod.load_config()) == "a"
    with pytest.raises(ConfigError):
        config_mod.set_active_account("missing")


def test_remove_account_and_active_reassignment(isolated_config):
    config_mod.save_account("a", {"url": "https://a.example.org", "username": "u1"})
    config_mod.save_account("b", {"url": "https://b.example.org", "username": "u2"})
    config_mod.set_active_account("b")
    removed = config_mod.remove_account("b")
    assert removed["username"] == "u2"
    cfg = config_mod.load_config()
    # No silent switch: the next account by name could be a full-profile
    # production account, so removing the active one leaves nothing active.
    assert config_mod.active_account_name(cfg) is None
    with pytest.raises(ConfigError):
        config_mod.remove_account("b")


def test_remove_last_account_clears_active(isolated_config):
    config_mod.save_account("a", {"url": "https://a.example.org", "username": "u1"})
    config_mod.remove_account("a")
    cfg = config_mod.load_config()
    assert config_mod.active_account_name(cfg) is None
    assert config_mod.get_accounts(cfg) == {}


def test_config_path_env_override(isolated_config):
    assert config_mod.config_path() == isolated_config
