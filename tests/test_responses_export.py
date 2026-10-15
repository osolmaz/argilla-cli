"""Unit tests for responses, suggestions, and datasets export/import."""

import json

import pytest
from argilla._models import SuggestionModel, UserResponseModel
from tests.helpers import invoke, set_profile

WS_SETTINGS = {
    "fields": [{"name": "prompt", "title": "Prompt", "required": True, "settings": {"type": "text"}}],
    "questions": [{"name": "quality", "title": "Quality", "required": True, "settings": {"type": "text"}}],
}


@pytest.fixture()
def dataset(fake_ctx):
    ws = fake_ctx.add_workspace("review")
    return fake_ctx.add_dataset("contracts", ws.id)


def seeded_dataset(fake_ctx, dataset):
    fake_ctx.add_field(dataset.id, "prompt")
    fake_ctx.add_question(dataset.id, "quality")
    return dataset


def make_response(question_id, value, user_id=None, status="submitted"):
    return UserResponseModel(values={str(question_id): {"value": value}}, status=status, user_id=user_id)


class TestResponsesSubmit:
    def test_submits_by_question_name(self, fake_ctx, dataset):
        seeded_dataset(fake_ctx, dataset)
        record = fake_ctx.add_record(dataset.id, "r1", fields={"prompt": "one"})
        result = invoke(
            fake_ctx,
            ["--json", "responses", "submit", "contracts", str(record.id), "--answers", '{"quality": "ok"}'],
        )
        assert result.exit_code == 0, result.output
        stored = fake_ctx.api.records.get(record.id)
        assert len(stored.responses) == 1
        assert stored.responses[0].status == "submitted"

    def test_submit_dry_run_sends_nothing(self, fake_ctx, dataset):
        seeded_dataset(fake_ctx, dataset)
        record = fake_ctx.add_record(dataset.id, "r1", fields={"prompt": "one"})
        result = invoke(
            fake_ctx,
            [
                "--json",
                "responses",
                "submit",
                "contracts",
                str(record.id),
                "--answers",
                '{"quality": "ok"}',
                "--dry-run",
            ],
        )
        assert result.exit_code == 0
        assert fake_ctx.api.records.get(record.id).responses == []

    def test_submit_unknown_question_fails(self, fake_ctx, dataset):
        record = fake_ctx.add_record(dataset.id, "r1", fields={"prompt": "one"})
        result = invoke(
            fake_ctx,
            ["responses", "submit", "contracts", str(record.id), "--answers", '{"nope": "x"}'],
        )
        assert result.exit_code == 1
        assert "Unknown question" in result.output

    def test_submit_by_external_id(self, fake_ctx, dataset):
        seeded_dataset(fake_ctx, dataset)
        fake_ctx.add_record(dataset.id, "r1", fields={"prompt": "one"})
        result = invoke(
            fake_ctx,
            ["--json", "responses", "submit", "contracts", "r1", "--answers", '{"quality": "ok"}'],
        )
        assert result.exit_code == 0, result.output

    def test_submit_files_under_the_caller(self, fake_ctx, dataset):
        seeded_dataset(fake_ctx, dataset)
        fake_ctx.add_user("amelie")
        record = fake_ctx.add_record(dataset.id, "r1", fields={"prompt": "one"})
        result = invoke(
            fake_ctx,
            [
                "--json",
                "responses",
                "submit",
                "contracts",
                str(record.id),
                "--answers",
                '{"quality": "ok"}',
            ],
        )
        assert result.exit_code == 0, result.output
        stored = fake_ctx.api.records.get(record.id)
        # The model carries no user_id: the 2.8 server pins the response to
        # the authenticated user and would ignore an override.
        stored_response = stored.responses[0]
        assert stored_response.values == {"quality": {"value": "ok"}}

    def test_submit_rejects_user_flag(self, fake_ctx, dataset, isolated_config):
        """The 2.8 server pins responses to the caller; --user cannot exist."""
        record = fake_ctx.add_record(dataset.id, "r1", fields={"prompt": "one"})
        result = invoke(
            fake_ctx,
            [
                "--json",
                "responses",
                "submit",
                "contracts",
                str(record.id),
                "--answers",
                '{"quality": "ok"}',
                "--user",
                "amelie",
            ],
        )
        assert result.exit_code != 0
        assert "no such option" in result.output.lower()

    def test_read_only_profile_refuses(self, fake_ctx, dataset, isolated_config):
        set_profile(isolated_config, "read-only")
        record = fake_ctx.add_record(dataset.id, "r1", fields={"prompt": "one"})
        result = invoke(
            fake_ctx,
            ["responses", "submit", "contracts", str(record.id), "--answers", '{"quality": "ok"}'],
        )
        assert result.exit_code == 1
        assert "read-only" in result.output


class TestResponsesList:
    def test_lists_responses_with_question_names(self, fake_ctx, dataset):
        seeded_dataset(fake_ctx, dataset)
        amelie = fake_ctx.add_user("amelie")
        record = fake_ctx.add_record(dataset.id, "r1", fields={"prompt": "one"})
        question = fake_ctx.api.questions.models[0]
        record.responses = [make_response(question.id, "ok", user_id=amelie.id)]
        result = invoke(fake_ctx, ["--json", "responses", "list", "contracts"])
        assert result.exit_code == 0, result.output
        rows = json.loads(result.stdout)
        assert len(rows) == 1
        assert rows[0]["question"] == "quality"
        assert rows[0]["value"] == "ok"
        assert rows[0]["user"] == "amelie"
        assert rows[0]["status"] == "submitted"

    def test_filters_by_user_and_status(self, fake_ctx, dataset):
        seeded_dataset(fake_ctx, dataset)
        amelie = fake_ctx.add_user("amelie")
        bob = fake_ctx.add_user("bob")
        record = fake_ctx.add_record(dataset.id, "r1", fields={"prompt": "one"})
        question = fake_ctx.api.questions.models[0]
        record.responses = [
            make_response(question.id, "ok", user_id=amelie.id, status="submitted"),
            make_response(question.id, "draft", user_id=bob.id, status="draft"),
        ]
        result = invoke(fake_ctx, ["--json", "responses", "list", "contracts", "--user", "bob", "--status", "draft"])
        assert result.exit_code == 0
        rows = json.loads(result.stdout)
        assert len(rows) == 1
        assert rows[0]["user"] == "bob"

    def test_empty_list(self, fake_ctx, dataset):
        result = invoke(fake_ctx, ["--json", "responses", "list", "contracts"])
        assert result.exit_code == 0
        assert json.loads(result.stdout) == []


class TestDatasetsExportImport:
    def test_export_writes_flat_jsonl(self, fake_ctx, dataset, tmp_path):
        seeded_dataset(fake_ctx, dataset)
        amelie = fake_ctx.add_user("amelie")
        record = fake_ctx.add_record(dataset.id, "r1", fields={"prompt": "one"}, metadata={"lang": "en"})
        question = fake_ctx.api.questions.models[0]
        record.suggestions = (SuggestionModel(question_id=question.id, value="ok"),)
        record.responses = [make_response(question.id, "ok", user_id=amelie.id)]
        target = tmp_path / "export.jsonl"
        result = invoke(fake_ctx, ["--json", "datasets", "export", "contracts", "-o", str(target)])
        assert result.exit_code == 0, result.output
        lines = [json.loads(line) for line in target.read_text().splitlines()]
        assert len(lines) == 1
        line = lines[0]
        assert line["id"] == "r1"
        assert line["prompt"] == "one"
        assert line["lang"] == "en"
        assert line["quality"] == "ok"  # the suggestion, flat
        assert line["responses"] == [{"question": "quality", "value": "ok", "user": "amelie", "status": "submitted"}]

    def test_export_refuses_to_overwrite(self, fake_ctx, dataset, tmp_path):
        target = tmp_path / "export.jsonl"
        target.write_text("")
        result = invoke(fake_ctx, ["datasets", "export", "contracts", "-o", str(target)])
        assert result.exit_code == 1
        assert "already exists" in result.output

    def test_import_roundtrips_an_export(self, fake_ctx, dataset, tmp_path):
        seeded_dataset(fake_ctx, dataset)
        amelie = fake_ctx.add_user("amelie")
        record = fake_ctx.add_record(dataset.id, "r1", fields={"prompt": "one"})
        question = fake_ctx.api.questions.models[0]
        record.responses = [make_response(question.id, "ok", user_id=amelie.id)]
        export_file = tmp_path / "export.jsonl"
        result = invoke(fake_ctx, ["datasets", "export", "contracts", "-o", str(export_file)])
        assert result.exit_code == 0, result.output

        # Import into a second dataset with the same schema.
        second = fake_ctx.add_dataset("briefs", dataset.workspace_id)
        fake_ctx.add_field(second.id, "prompt")
        fake_ctx.add_question(second.id, "quality")
        result = invoke(fake_ctx, ["--json", "datasets", "import", "briefs", str(export_file)])
        assert result.exit_code == 0, result.output
        row = json.loads(result.stdout)
        assert row["records"] == 1
        assert row["responses_kept_for_review"] == 1
        imported = [m for m in fake_ctx.api.records.models if m.external_id == "r1" and m.id != record.id]
        assert len(imported) == 1
        assert imported[0].fields["prompt"] == "one"

    def test_import_dry_run(self, fake_ctx, dataset, tmp_path):
        export_file = tmp_path / "export.jsonl"
        export_file.write_text(json.dumps({"id": "r9", "prompt": "x"}) + "\n")
        result = invoke(fake_ctx, ["--json", "datasets", "import", "contracts", str(export_file), "--dry-run"])
        assert result.exit_code == 0
        row = json.loads(result.stdout)
        assert row["dry_run"] is True
        assert fake_ctx.api.records.models == []

    def test_import_refused_by_read_only(self, fake_ctx, dataset, isolated_config, tmp_path):
        set_profile(isolated_config, "read-only")
        export_file = tmp_path / "export.jsonl"
        export_file.write_text(json.dumps({"id": "r9", "prompt": "x"}) + "\n")
        result = invoke(fake_ctx, ["datasets", "import", "contracts", str(export_file)])
        assert result.exit_code == 1
        assert "read-only" in result.output


class TestSuggestionsAdd:
    def test_add_from_file(self, fake_ctx, dataset, tmp_path):
        seeded_dataset(fake_ctx, dataset)
        record = fake_ctx.add_record(dataset.id, "r1", fields={"prompt": "one"})
        suggestion_file = tmp_path / "suggestions.jsonl"
        suggestion_file.write_text(json.dumps({"id": str(record.id), "quality": "ok"}) + "\n")
        result = invoke(fake_ctx, ["--json", "suggestions", "add", "contracts", "--file", str(suggestion_file)])
        assert result.exit_code == 0, result.output
        stored = fake_ctx.api.records.get(record.id)
        assert len(stored.suggestions) == 1
        assert stored.suggestions[0].value == "ok"
        # The record's fields survive the upsert.
        assert stored.fields == {"prompt": "one"}

    def test_add_does_not_clobber_fields(self, fake_ctx, dataset, tmp_path):
        seeded_dataset(fake_ctx, dataset)
        record = fake_ctx.add_record(dataset.id, "r1", fields={"prompt": "one"})
        suggestion_file = tmp_path / "suggestions.json"
        suggestion_file.write_text(json.dumps({"id": "r1", "suggestions": {"quality": "ok"}}))
        result = invoke(fake_ctx, ["suggestions", "add", "contracts", "--file", str(suggestion_file)])
        assert result.exit_code == 0, result.output
        stored = fake_ctx.api.records.get(record.id)
        assert stored.fields == {"prompt": "one"}

    def test_add_dry_run(self, fake_ctx, dataset, tmp_path):
        seeded_dataset(fake_ctx, dataset)
        record = fake_ctx.add_record(dataset.id, "r1", fields={"prompt": "one"})
        suggestion_file = tmp_path / "suggestions.jsonl"
        suggestion_file.write_text(json.dumps({"id": str(record.id), "quality": "ok"}) + "\n")
        result = invoke(
            fake_ctx,
            ["--json", "suggestions", "add", "contracts", "--file", str(suggestion_file), "--dry-run"],
        )
        assert result.exit_code == 0, result.output
        row = json.loads(result.stdout)
        assert row["dry_run"] is True
        assert not fake_ctx.api.records.get(record.id).suggestions

    def test_add_unknown_question_fails(self, fake_ctx, dataset, tmp_path):
        record = fake_ctx.add_record(dataset.id, "r1", fields={"prompt": "one"})
        suggestion_file = tmp_path / "suggestions.jsonl"
        suggestion_file.write_text(json.dumps({"id": str(record.id), "nope": "x"}) + "\n")
        result = invoke(fake_ctx, ["suggestions", "add", "contracts", "--file", str(suggestion_file)])
        assert result.exit_code == 1
        assert "Unknown question" in result.output
