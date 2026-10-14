"""Unit tests for datasets create/update/delete/progress and records commands."""

import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from tests.helpers import invoke, set_profile

WS_SETTINGS = {
    "fields": [{"name": "prompt", "title": "Prompt", "required": True, "settings": {"type": "text"}}],
    "questions": [{"name": "quality", "title": "Quality", "required": True, "settings": {"type": "text"}}],
}


@pytest.fixture()
def dataset(fake_ctx):
    ws = fake_ctx.add_workspace("review")
    return fake_ctx.add_dataset("contracts", ws.id)


class TestDatasetsCreate:
    def test_creates_from_settings_file(self, fake_ctx, tmp_path):
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps(WS_SETTINGS))
        fake_ctx.add_workspace("review")
        result = invoke(
            fake_ctx,
            ["--json", "datasets", "create", "briefs", "--workspace", "review", "--settings", str(settings_file)],
        )
        assert result.exit_code == 0, result.output
        row = json.loads(result.stdout)
        assert row["action"] == "create"
        names = [model.name for model in fake_ctx.api.datasets.models]
        assert names == ["contracts", "briefs"] or names == ["briefs"]

    def test_settings_fields_reach_the_server(self, fake_ctx, tmp_path):
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps(WS_SETTINGS))
        fake_ctx.add_workspace("review")
        result = invoke(
            fake_ctx, ["datasets", "create", "briefs", "--workspace", "review", "--settings", str(settings_file)]
        )
        assert result.exit_code == 0, result.output
        field_names = [model.name for model in fake_ctx.api.fields.models]
        question_names = [model.name for model in fake_ctx.api.questions.models]
        assert field_names == ["prompt"]
        assert question_names == ["quality"]

    def test_yaml_settings(self, fake_ctx, tmp_path):
        settings_file = tmp_path / "settings.yaml"
        settings_file.write_text(
            "fields:\n  - name: prompt\n    title: Prompt\n    required: true\n    settings: {type: text}\n"
            "questions:\n  - name: quality\n    title: Quality\n    required: true\n    settings: {type: text}\n"
        )
        fake_ctx.add_workspace("review")
        result = invoke(
            fake_ctx,
            ["--json", "datasets", "create", "briefs", "--workspace", "review", "--settings", str(settings_file)],
        )
        assert result.exit_code == 0, result.output
        assert [model.name for model in fake_ctx.api.fields.models] == ["prompt"]

    def test_like_copies_settings(self, fake_ctx, dataset):
        fake_ctx.add_field(dataset.id, "prompt")
        fake_ctx.add_question(dataset.id, "quality")
        result = invoke(fake_ctx, ["--json", "datasets", "create", "briefs", "--like", "contracts"])
        assert result.exit_code == 0, result.output
        assert json.loads(result.stdout)["action"] == "create"

    def test_like_missing_dataset_fails(self, fake_ctx):
        fake_ctx.add_workspace("review")
        result = invoke(fake_ctx, ["datasets", "create", "briefs", "--like", "ghost"])
        assert result.exit_code == 1
        assert "No dataset named 'ghost'" in result.output

    def test_settings_and_like_conflict(self, fake_ctx, dataset, tmp_path):
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps(WS_SETTINGS))
        result = invoke(
            fake_ctx,
            ["--json", "datasets", "create", "briefs", "--like", "contracts", "--settings", str(settings_file)],
        )
        assert result.exit_code == 1
        assert "not both" in result.output

    def test_duplicate_name_fails(self, fake_ctx, dataset):
        result = invoke(fake_ctx, ["datasets", "create", "contracts"])
        assert result.exit_code == 1
        assert "already exists" in result.output

    def test_dry_run_creates_nothing(self, fake_ctx, tmp_path):
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps(WS_SETTINGS))
        fake_ctx.add_workspace("review")
        result = invoke(
            fake_ctx,
            [
                "--json",
                "datasets",
                "create",
                "briefs",
                "--workspace",
                "review",
                "--settings",
                str(settings_file),
                "--dry-run",
            ],
        )
        assert result.exit_code == 0
        row = json.loads(result.stdout)
        assert row["dry_run"] is True
        assert row["workspace"] == "review"
        assert fake_ctx.api.datasets.models == []

    def test_read_only_profile_refuses(self, fake_ctx, isolated_config):
        set_profile(isolated_config, "read-only")
        result = invoke(fake_ctx, ["datasets", "create", "briefs"])
        assert result.exit_code == 1
        assert "read-only" in result.output


class TestDatasetsUpdate:
    def test_updates_settings(self, fake_ctx, dataset):
        settings_file = fake_ctx_settings(fake_ctx, dataset)
        result = invoke(fake_ctx, ["--json", "datasets", "update", "contracts", "--settings", str(settings_file)])
        assert result.exit_code == 0, result.output
        assert json.loads(result.stdout)["action"] == "update"

    def test_updates_guidelines_only(self, fake_ctx, dataset):
        fake_ctx.add_field(dataset.id, "prompt")
        fake_ctx.add_question(dataset.id, "quality")
        result = invoke(fake_ctx, ["--json", "datasets", "update", "contracts", "--guidelines", "Be kind."])
        assert result.exit_code == 0, result.output
        stored = fake_ctx.api.datasets.get(dataset.id)
        assert stored.guidelines == "Be kind."

    def test_nothing_to_update_fails(self, fake_ctx, dataset):
        result = invoke(fake_ctx, ["datasets", "update", "contracts"])
        assert result.exit_code == 1
        assert "Nothing to update" in result.output


def fake_ctx_settings(fake_ctx, dataset):
    fake_ctx.add_field(dataset.id, "prompt")
    fake_ctx.add_question(dataset.id, "quality")
    doc = {
        "fields": [{"name": "prompt2", "title": "Prompt2", "required": True, "settings": {"type": "text"}}],
        "questions": [{"name": "quality2", "title": "Quality2", "required": True, "settings": {"type": "text"}}],
    }
    settings_file = Path("/tmp") / f"settings-{uuid4()}.json"
    settings_file.write_text(json.dumps(doc))
    return settings_file


class TestDatasetsDeleteAndProgress:
    def test_delete_with_yes(self, fake_ctx, dataset):
        result = invoke(fake_ctx, ["--json", "datasets", "delete", "contracts", "--yes"])
        assert result.exit_code == 0
        assert fake_ctx.api.datasets.models == []

    def test_delete_without_yes_aborts(self, fake_ctx, dataset):
        result = invoke(fake_ctx, ["datasets", "delete", "contracts"], input="n\n")
        assert result.exit_code != 0

    def test_progress(self, fake_ctx, dataset):
        fake_ctx.api.datasets.get_progress = lambda **kwargs: SimpleNamespace(
            model_dump=lambda: {"total": 2, "completed": 1, "pending": 1}
        )
        result = invoke(fake_ctx, ["--json", "datasets", "progress", "contracts"])
        assert result.exit_code == 0
        row = json.loads(result.stdout)
        assert row["total"] == 2

    def test_progress_with_users(self, fake_ctx, dataset):
        calls = {}

        def list_users_progress(**kwargs):
            calls.update(kwargs)
            return []

        fake_ctx.api.datasets.get_progress = lambda **kwargs: SimpleNamespace(
            model_dump=lambda: {"total": 0, "completed": 0, "pending": 0}
        )
        fake_ctx.api.datasets.list_users_progress = list_users_progress
        result = invoke(fake_ctx, ["--json", "datasets", "progress", "contracts", "--with-users"])
        assert result.exit_code == 0
        row = json.loads(result.stdout)
        assert row == {"total": 0, "completed": 0, "pending": 0, "users": {}}


class TestRecordsAdd:
    def test_add_jsonl_file(self, fake_ctx, dataset, tmp_path):
        fake_ctx.add_field(dataset.id, "prompt")
        fake_ctx.add_question(dataset.id, "quality")
        records_file = tmp_path / "records.jsonl"
        records_file.write_text(
            json.dumps({"id": "r1", "prompt": "one"}) + "\n" + json.dumps({"id": "r2", "prompt": "two"}) + "\n"
        )
        result = invoke(fake_ctx, ["--json", "records", "add", "contracts", "--file", str(records_file)])
        assert result.exit_code == 0, result.output
        assert json.loads(result.stdout)["records"] == 2
        assert len(fake_ctx.api.records.models) == 2

    def test_add_is_an_upsert_by_id(self, fake_ctx, dataset, tmp_path):
        fake_ctx.add_field(dataset.id, "prompt")
        fake_ctx.add_question(dataset.id, "quality")
        first = tmp_path / "first.jsonl"
        second = tmp_path / "second.jsonl"
        first.write_text(json.dumps({"id": "r1", "prompt": "one"}) + "\n")
        second.write_text(json.dumps({"id": "r1", "prompt": "rewritten"}) + "\n")
        invoke(fake_ctx, ["records", "add", "contracts", "--file", str(first)])
        result = invoke(fake_ctx, ["--json", "records", "add", "contracts", "--file", str(second)])
        assert result.exit_code == 0
        assert len(fake_ctx.api.records.models) == 1
        assert fake_ctx.api.records.models[0].fields["prompt"] == "rewritten"

    def test_add_single_json_object(self, fake_ctx, dataset, tmp_path):
        fake_ctx.add_field(dataset.id, "prompt")
        fake_ctx.add_question(dataset.id, "quality")
        records_file = tmp_path / "one.json"
        records_file.write_text(json.dumps({"id": "r1", "prompt": "one"}))
        result = invoke(fake_ctx, ["--json", "records", "add", "contracts", "--file", str(records_file)])
        assert result.exit_code == 0
        assert json.loads(result.stdout)["records"] == 1

    def test_add_requires_file(self, fake_ctx, dataset):
        result = invoke(fake_ctx, ["records", "add", "contracts"])
        assert result.exit_code == 1
        assert "--file" in result.output

    def test_add_dry_run_sends_nothing(self, fake_ctx, dataset, tmp_path):
        records_file = tmp_path / "records.jsonl"
        records_file.write_text(json.dumps({"id": "r1", "prompt": "one"}) + "\n")
        result = invoke(fake_ctx, ["--json", "records", "add", "contracts", "--file", str(records_file), "--dry-run"])
        assert result.exit_code == 0
        row = json.loads(result.stdout)
        assert row["dry_run"] is True
        assert fake_ctx.api.records.models == []

    def test_add_empty_file_fails(self, fake_ctx, dataset, tmp_path):
        records_file = tmp_path / "records.jsonl"
        records_file.write_text("\n\n")
        result = invoke(fake_ctx, ["records", "add", "contracts", "--file", str(records_file)])
        assert result.exit_code == 1
        assert "No records" in result.output

    def test_add_invalid_json_fails(self, fake_ctx, dataset, tmp_path):
        records_file = tmp_path / "records.json"
        records_file.write_text("{not json")
        result = invoke(fake_ctx, ["records", "add", "contracts", "--file", str(records_file)])
        assert result.exit_code == 1

    def test_read_only_profile_refuses(self, fake_ctx, dataset, isolated_config, tmp_path):
        set_profile(isolated_config, "read-only")
        records_file = tmp_path / "records.jsonl"
        records_file.write_text(json.dumps({"id": "r1", "prompt": "one"}) + "\n")
        result = invoke(fake_ctx, ["records", "add", "contracts", "--file", str(records_file)])
        assert result.exit_code == 1
        assert "read-only" in result.output


class TestRecordsListAndGet:
    def test_list_as_table(self, fake_ctx, dataset):
        fake_ctx.add_record(dataset.id, "r1", fields={"prompt": "one"})
        fake_ctx.add_record(dataset.id, "r2", fields={"prompt": "two"})
        result = invoke(fake_ctx, ["records", "list", "contracts"])
        assert result.exit_code == 0
        assert "r1" in result.output
        assert "r2" in result.output

    def test_list_json_carries_fields(self, fake_ctx, dataset):
        fake_ctx.add_record(dataset.id, "r1", fields={"prompt": "one"}, metadata={"lang": "en"})
        result = invoke(fake_ctx, ["--json", "records", "list", "contracts"])
        assert result.exit_code == 0
        rows = json.loads(result.stdout)
        assert rows[0]["external_id"] == "r1"
        assert rows[0]["fields"] == {"prompt": "one"}
        assert rows[0]["metadata"] == {"lang": "en"}

    def test_list_limit_and_offset(self, fake_ctx, dataset):
        for index in range(5):
            fake_ctx.add_record(dataset.id, f"r{index}")
        result = invoke(fake_ctx, ["--json", "records", "list", "contracts", "--limit", "2", "--offset", "2"])
        assert result.exit_code == 0
        rows = json.loads(result.stdout)
        assert [row["external_id"] for row in rows] == ["r2", "r3"]

    def test_get_by_server_id(self, fake_ctx, dataset):
        model = fake_ctx.add_record(dataset.id, "r1", fields={"prompt": "one"})
        result = invoke(fake_ctx, ["--json", "records", "get", "contracts", str(model.id)])
        assert result.exit_code == 0, result.output
        row = json.loads(result.stdout)
        assert row["external_id"] == "r1"
        assert row["fields"] == {"prompt": "one"}

    def test_get_bad_uuid_fails(self, fake_ctx, dataset):
        result = invoke(fake_ctx, ["records", "get", "contracts", "not-a-uuid"])
        assert result.exit_code == 1
        assert "not a record id" in result.output


class TestRecordsUpdateAndDelete:
    def test_update_fields(self, fake_ctx, dataset):
        model = fake_ctx.add_record(dataset.id, "r1", fields={"prompt": "old"})
        result = invoke(
            fake_ctx,
            ["--json", "records", "update", "contracts", str(model.id), "--fields", json.dumps({"prompt": "new"})],
        )
        assert result.exit_code == 0, result.output
        row = json.loads(result.stdout)
        assert row["fields"] == {"prompt": "new"}

    def test_update_metadata(self, fake_ctx, dataset):
        model = fake_ctx.add_record(dataset.id, "r1", fields={"prompt": "x"})
        result = invoke(
            fake_ctx,
            ["--json", "records", "update", "contracts", str(model.id), "--metadata", json.dumps({"lang": "en"})],
        )
        assert result.exit_code == 0
        stored = fake_ctx.api.records.get(model.id)
        assert {item.name: item.value for item in stored.metadata} == {"lang": "en"}

    def test_update_nothing_fails(self, fake_ctx, dataset):
        model = fake_ctx.add_record(dataset.id, "r1", fields={"prompt": "x"})
        result = invoke(fake_ctx, ["records", "update", "contracts", str(model.id)])
        assert result.exit_code == 1
        assert "Nothing to update" in result.output

    def test_delete_with_yes(self, fake_ctx, dataset):
        model = fake_ctx.add_record(dataset.id, "r1", fields={"prompt": "x"})
        result = invoke(fake_ctx, ["--json", "records", "delete", "contracts", str(model.id), "--yes"])
        assert result.exit_code == 0
        assert fake_ctx.api.records.models == []

    def test_delete_without_yes_aborts(self, fake_ctx, dataset):
        model = fake_ctx.add_record(dataset.id, "r1", fields={"prompt": "x"})
        result = invoke(fake_ctx, ["records", "delete", "contracts", str(model.id)], input="n\n")
        assert result.exit_code != 0
        assert len(fake_ctx.api.records.models) == 1
