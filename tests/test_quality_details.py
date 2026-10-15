"""Unit tests for small helpers, added to raise the mutation kill rate."""

import json

import httpx
import pytest
from tests.helpers import invoke

from argilla_cli import auth
from argilla_cli.commands.core import _release
from argilla_cli.output import _cell, _print_table, emit
from argilla_cli.profiles import is_write


class FakeCtx:
    def __init__(self, obj=None, params=None):
        self.obj = obj or {}
        self.params = params or {}


def isolated_config_path():

    from argilla_cli import config as config_mod

    return config_mod.config_path()


class TestRelease:
    @pytest.mark.parametrize(
        ("version", "expected"),
        [
            ("2.8.0", "2.8.0"),
            ("2.8.0.dev0", "2.8.0"),
            ("2.8.0.dev0\n", "2.8.0"),
            ("2.9.1rc1", "2.9.1"),
            ("3.0.0a2", "3.0.0"),
            ("3.0.0b3", "3.0.0"),
            ("2.8.0+local", "2.8.0"),
        ],
    )
    def test_strips_dev_and_prerelease_suffixes(self, version, expected):
        assert _release(version) == expected


class TestCellRendering:
    def test_none_is_empty(self):
        assert _cell(None) == ""

    def test_bool_is_lowercase(self):
        assert _cell(True) == "true"
        assert _cell(False) == "false"

    def test_structures_are_json(self):
        assert _cell({"a": 1}) == '{"a": 1}'
        assert _cell([1, 2]) == "[1, 2]"

    def test_strings_pass_through(self):
        assert _cell("x") == "x"
        assert _cell(7) == "7"


class TestPrintTable:
    def test_column_fallback_uses_first_row_keys(self, capsys):
        _print_table([{"a": 1, "b": 2}])
        out = capsys.readouterr().out
        assert "a" in out and "b" in out

    def test_missing_key_renders_empty(self, capsys):
        _print_table([{"a": 1}], columns=[("missing", "MISSING")])
        out = capsys.readouterr().out
        lines = out.splitlines()
        assert lines[0].strip() == "MISSING"
        # The row's value under the missing key renders as an empty cell.
        assert lines[-1].strip() == ""


class TestEmitTableWithoutContext:
    def test_emit_with_no_context_prints_table(self, capsys):
        emit(None, [{"name": "ws"}], columns=[("name", "NAME")])
        out = capsys.readouterr().out
        assert "NAME" in out and "ws" in out


class TestIsWrite:
    def test_read_paths_are_not_writes(self):
        assert not is_write("whoami")
        assert not is_write("datasets list")
        assert not is_write("datasets export")

    def test_write_paths(self):
        assert is_write("datasets create")
        assert is_write("responses submit")
        assert is_write("workspaces add-user")


class TestReviewRegressions:
    """Regressions from the code review pass: credentials, scoping, output."""

    def test_flag_url_never_borrows_an_account_secret(self, isolated_config):
        isolated_config.parent.mkdir(parents=True, exist_ok=True)
        isolated_config.write_text(
            json.dumps(
                {
                    "active_account": "a",
                    "accounts": {"a": {"url": "https://a.example.org", "api_key": "key-a"}},
                }
            )
        )
        with pytest.raises(auth.CredentialsError, match="must not be sent"):
            auth.resolve_credentials(FakeCtx(obj={"url": "https://c.example.org"}))

    def test_account_secret_without_url_flag_applies_to_the_account(self, isolated_config):
        isolated_config.parent.mkdir(parents=True, exist_ok=True)
        isolated_config.write_text(
            json.dumps(
                {
                    "active_account": "a",
                    "accounts": {"a": {"url": "https://a.example.org", "api_key": "key-a"}},
                }
            )
        )
        creds = auth.resolve_credentials(FakeCtx(obj={"api_key": "key-fresh"}))
        assert creds.url == "https://a.example.org"
        assert creds.api_key == "key-fresh"

    def test_save_account_replaces_secrets(self, isolated_config):
        from argilla_cli import config as config_mod

        config_mod.save_account("a", {"url": "https://a.example.org", "api_key": "old-key"})
        config_mod.save_account("a", {"url": "https://a.example.org", "username": "amelie"})
        entry = config_mod.get_account(config_mod.load_config(), "a")
        assert "api_key" not in entry

    def test_named_account_that_does_not_exist_fails(self, isolated_config):
        with pytest.raises(auth.CredentialsError, match="No account named 'nope'"):
            auth.resolve_credentials(FakeCtx(obj={"account": "nope"}))

    def test_token_redirect_is_refused(self, mock_http):
        def handler(request):
            return httpx.Response(307, headers={"Location": "https://evil.example.org/catch?x=1"})

        mock_http(handler)
        with pytest.raises(auth.CredentialsError, match="redirect"):
            auth.request_token("https://redirector.example.org", "amelie", "pw")

    def test_non_json_me_answer_is_a_credential_error(self, mock_http):
        def handler(request):
            return httpx.Response(200, text="<html>login page</html>")

        mock_http(handler)
        with pytest.raises(auth.CredentialsError, match="Argilla API response"):
            auth.login_with_api_key("https://proxy.example.org", "key")

    def test_ambiguous_dataset_name_is_refused(self, fake_ctx):
        ws1 = fake_ctx.add_workspace("one")
        ws2 = fake_ctx.add_workspace("two")
        fake_ctx.add_dataset("contracts", ws1.id)
        fake_ctx.add_dataset("contracts", ws2.id)
        result = invoke(fake_ctx, ["--json", "datasets", "get", "contracts"])
        assert result.exit_code != 0
        assert "--workspace" in result.output

    def test_workspace_flag_disambiguates(self, fake_ctx):
        ws1 = fake_ctx.add_workspace("one")
        ws2 = fake_ctx.add_workspace("two")
        fake_ctx.add_dataset("contracts", ws1.id)
        other = fake_ctx.add_dataset("contracts", ws2.id)
        result = invoke(fake_ctx, ["--json", "datasets", "get", "contracts", "--workspace", "two"])
        assert result.exit_code == 0, result.output
        assert json.loads(result.output)["id"] == str(other.id)

    def test_record_of_another_dataset_is_refused(self, fake_ctx):
        ws = fake_ctx.add_workspace("review")
        dataset_a = fake_ctx.add_dataset("ds-a", ws.id)
        dataset_b = fake_ctx.add_dataset("ds-b", ws.id)
        fake_ctx.add_question(dataset_a.id, "quality")
        fake_ctx.add_question(dataset_b.id, "quality")
        record = fake_ctx.add_record(dataset_b.id, "r1", fields={"prompt": "x"})
        result = invoke(
            fake_ctx,
            ["--json", "responses", "submit", "ds-a", str(record.id), "--answers", '{"quality": "ok"}'],
        )
        assert result.exit_code != 0
        assert "ds-a" in result.output
        assert fake_ctx.api.records.get(record.id).responses in (None, [])

    def test_uuid_shaped_external_id_resolves_as_external(self, fake_ctx):
        from uuid import uuid4

        ws = fake_ctx.add_workspace("review")
        dataset = fake_ctx.add_dataset("contracts", ws.id)
        fake_ctx.add_question(dataset.id, "quality")
        fake_uuid = str(uuid4())
        record = fake_ctx.add_record(dataset.id, fake_uuid, fields={"prompt": "x"})
        result = invoke(
            fake_ctx,
            ["--json", "responses", "submit", "contracts", fake_uuid, "--answers", '{"quality": "ok"}'],
        )
        assert result.exit_code == 0, result.output
        assert fake_ctx.api.records.get(record.id).responses

    def test_global_yes_skips_confirmation(self, fake_ctx):
        ws = fake_ctx.add_workspace("review")
        dataset = fake_ctx.add_dataset("contracts", ws.id)
        record = fake_ctx.add_record(dataset.id, "r1", fields={"prompt": "x"})
        result = invoke(
            fake_ctx,
            ["--yes", "records", "delete", "contracts", str(record.id), "--yes"],
        )
        assert result.exit_code == 0, result.output
        assert fake_ctx.api.records.get(record.id) is None

    def test_profile_flag_cannot_loosen_read_only(self, fake_ctx):
        from tests.helpers import set_profile

        set_profile(isolated_config_path(), "read-only")
        result = invoke(fake_ctx, ["--profile", "full", "--json", "workspaces", "create", "nope"])
        assert result.exit_code != 0
        assert fake_ctx.api.workspaces.models == []

    def test_settings_dump_carries_no_server_ids(self, fake_ctx):
        ws = fake_ctx.add_workspace("review")
        dataset = fake_ctx.add_dataset("contracts", ws.id)
        fake_ctx.add_field(dataset.id, "prompt")
        fake_ctx.add_question(dataset.id, "quality")
        result = invoke(fake_ctx, ["--json", "datasets", "get", "contracts", "--settings"])
        document = json.loads(result.stdout)
        for key in ("id", "dataset_id", "inserted_at", "updated_at"):
            assert key not in document
            for group in ("fields", "questions"):
                for item in document[group]:
                    assert key not in item

    def test_update_with_a_dump_from_another_dataset_touches_only_the_target(self, fake_ctx, tmp_path):
        ws = fake_ctx.add_workspace("review")
        source = fake_ctx.add_dataset("source", ws.id)
        target = fake_ctx.add_dataset("target", ws.id)
        fake_ctx.add_field(source.id, "prompt")
        fake_ctx.add_question(source.id, "quality")
        fake_ctx.add_field(target.id, "prompt")
        fake_ctx.add_question(target.id, "quality")
        dump = tmp_path / "settings.json"
        result = invoke(fake_ctx, ["--json", "datasets", "get", "source", "--settings", "-o", str(dump)])
        assert result.exit_code == 0, result.output
        result = invoke(fake_ctx, ["--json", "datasets", "update", "target", "--settings", str(dump)])
        assert result.exit_code == 0, result.output
        source_fields = [model.name for model in fake_ctx.api.fields.models if model.dataset_id == source.id]
        target_fields = [model.name for model in fake_ctx.api.fields.models if model.dataset_id == target.id]
        assert sorted(source_fields) == ["prompt"]
        assert sorted(target_fields) == ["prompt"]

    def test_subcommand_json_flag(self, fake_ctx):
        result = invoke(fake_ctx, ["datasets", "list", "--json"])
        assert result.exit_code == 0, result.output
        json.loads(result.output)

    def test_jsonl_from_stdin(self, fake_ctx):
        ws = fake_ctx.add_workspace("review")
        dataset = fake_ctx.add_dataset("contracts", ws.id)
        fake_ctx.add_field(dataset.id, "prompt")
        fake_ctx.add_question(dataset.id, "quality")
        payload = json.dumps({"id": "r1", "prompt": "one"}) + "\n" + json.dumps({"id": "r2", "prompt": "two"}) + "\n"
        result = invoke(fake_ctx, ["--json", "records", "add", "contracts", "--file", "-"], input=payload)
        assert result.exit_code == 0, result.output
        assert len(fake_ctx.api.records.models) == 2

    def test_users_create_does_not_log_in_as_the_new_user(self, fake_ctx, monkeypatch):
        """The command's --username/--password name the new user, not the caller."""
        import argilla_cli.auth as auth_mod

        seen: list[str] = []
        original = auth_mod.build_client

        def spy(creds):
            seen.append(creds.username or "")
            return original(creds)

        monkeypatch.setattr(auth_mod, "build_client", spy)
        result = invoke(
            fake_ctx,
            ["--json", "users", "create", "--username", "newbie", "--password", "newbie-pw"],
        )
        assert result.exit_code == 0, result.output
        assert seen and seen[0] != "newbie"
