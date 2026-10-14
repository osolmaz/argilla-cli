"""Unit tests for the workspaces, users, and datasets commands.

Each test drives real ``argilla`` resource classes over the in-memory fake
client (see conftest.py for the fake_ctx and invoke fixtures), so no network
is involved.
"""

import json
from uuid import uuid4

from tests.helpers import invoke

WS_ID = uuid4()
USER_ID = uuid4()
DS_ID = uuid4()


class TestWorkspacesList:
    def test_lists_all_workspaces_as_table(self, fake_ctx):
        fake_ctx.add_workspace("review")
        fake_ctx.add_workspace("archive")
        result = invoke(fake_ctx, ["workspaces", "list"])
        assert result.exit_code == 0
        assert "review" in result.output
        assert "archive" in result.output

    def test_lists_workspaces_as_json(self, fake_ctx):
        fake_ctx.add_workspace("review")
        result = invoke(fake_ctx, ["--json", "workspaces", "list"])
        assert result.exit_code == 0
        rows = json.loads(result.output)
        assert [row["name"] for row in rows] == ["review"]
        assert rows[0]["id"]


class TestWorkspacesCreate:
    def test_creates_workspace(self, fake_ctx):
        result = invoke(fake_ctx, ["workspaces", "create", "review"])
        assert result.exit_code == 0
        names = [ws.name for ws in fake_ctx.api.workspaces.models]
        assert names == ["review"]

    def test_create_is_json(self, fake_ctx):
        result = invoke(fake_ctx, ["--json", "workspaces", "create", "review"])
        assert result.exit_code == 0
        row = json.loads(result.output)
        assert row["name"] == "review"
        assert row["id"] is not None

    def test_create_refuses_duplicate(self, fake_ctx):
        fake_ctx.add_workspace("review")
        result = invoke(fake_ctx, ["workspaces", "create", "review"])
        assert result.exit_code == 1
        assert "already exists" in result.output

    def test_create_dry_run_makes_no_changes(self, fake_ctx):
        result = invoke(fake_ctx, ["--json", "workspaces", "create", "review", "--dry-run"])
        assert result.exit_code == 0
        row = json.loads(result.output)
        assert row["dry_run"] is True
        assert fake_ctx.api.workspaces.models == []


class TestWorkspacesDelete:
    def test_deletes_workspace(self, fake_ctx):
        fake_ctx.add_workspace("review")
        result = invoke(fake_ctx, ["workspaces", "delete", "review", "--yes"])
        assert result.exit_code == 0
        assert fake_ctx.api.workspaces.models == []

    def test_delete_without_yes_aborts(self, fake_ctx):
        fake_ctx.add_workspace("review")
        result = invoke(fake_ctx, ["workspaces", "delete", "review"], input="n\n")
        assert result.exit_code != 0
        assert len(fake_ctx.api.workspaces.models) == 1

    def test_delete_missing_workspace_fails(self, fake_ctx):
        result = invoke(fake_ctx, ["workspaces", "delete", "ghost", "--yes"])
        assert result.exit_code == 1
        assert "No workspace named 'ghost'" in result.output


class TestWorkspaceMembers:
    def test_add_user(self, fake_ctx):
        fake_ctx.add_workspace("review")
        fake_ctx.add_user("amelie")
        result = invoke(fake_ctx, ["--json", "workspaces", "add-user", "review", "amelie"])
        assert result.exit_code == 0
        assert json.loads(result.output)["action"] == "add-user"

    def test_add_unknown_user_fails(self, fake_ctx):
        fake_ctx.add_workspace("review")
        result = invoke(fake_ctx, ["workspaces", "add-user", "review", "ghost"])
        assert result.exit_code == 1
        assert "No user named 'ghost'" in result.output

    def test_remove_user(self, fake_ctx):
        fake_ctx.add_workspace("review")
        fake_ctx.add_user("amelie")
        result = invoke(fake_ctx, ["--json", "workspaces", "remove-user", "review", "amelie", "--yes"])
        assert result.exit_code == 0
        assert json.loads(result.output)["action"] == "remove-user"

    def test_remove_user_without_yes_aborts(self, fake_ctx):
        fake_ctx.add_workspace("review")
        fake_ctx.add_user("amelie")
        result = invoke(fake_ctx, ["workspaces", "remove-user", "review", "amelie"], input="n\n")
        assert result.exit_code != 0


class TestUsersListAndGet:
    def test_lists_users(self, fake_ctx):
        fake_ctx.add_user("amelie", role="owner")
        result = invoke(fake_ctx, ["--json", "users", "list"])
        assert result.exit_code == 0
        rows = json.loads(result.output)
        assert [row["username"] for row in rows] == ["amelie"]
        assert rows[0]["role"] == "owner"

    def test_get_user(self, fake_ctx):
        fake_ctx.add_user("amelie", first_name="Amelie")
        result = invoke(fake_ctx, ["--json", "users", "get", "amelie"])
        assert result.exit_code == 0
        row = json.loads(result.output)
        assert row["username"] == "amelie"
        assert row["first_name"] == "Amelie"

    def test_get_missing_user_fails(self, fake_ctx):
        result = invoke(fake_ctx, ["users", "get", "ghost"])
        assert result.exit_code == 1
        assert "No user named 'ghost'" in result.output


class TestUsersCreate:
    def test_creates_user(self, fake_ctx):
        result = invoke(
            fake_ctx,
            ["--json", "users", "create", "--username", "bob", "--password", "pw-bob", "--role", "annotator"],
        )
        assert result.exit_code == 0
        row = json.loads(result.output)
        assert row["username"] == "bob"
        assert row["role"] == "annotator"
        assert [user.username for user in fake_ctx.api.users.models] == ["bob"]

    def test_create_prompts_for_password(self, fake_ctx):
        result = invoke(fake_ctx, ["users", "create", "--username", "bob"], input="pw-bob\n")
        assert result.exit_code == 0
        assert [user.username for user in fake_ctx.api.users.models] == ["bob"]

    def test_create_dry_run_makes_no_changes(self, fake_ctx):
        result = invoke(
            fake_ctx,
            ["--json", "users", "create", "--username", "bob", "--password", "pw-bob", "--dry-run"],
        )
        assert result.exit_code == 0
        assert json.loads(result.output)["dry_run"] is True
        assert fake_ctx.api.users.models == []

    def test_create_bad_role_fails(self, fake_ctx):
        result = invoke(fake_ctx, ["users", "create", "--username", "bob", "--password", "pw", "--role", "boss"])
        assert result.exit_code == 2


class TestUsersDelete:
    def test_deletes_user(self, fake_ctx):
        fake_ctx.add_user("bob")
        result = invoke(fake_ctx, ["--json", "users", "delete", "bob", "--yes"])
        assert result.exit_code == 0
        assert json.loads(result.output)["action"] == "delete"
        assert fake_ctx.api.users.models == []

    def test_delete_without_yes_aborts(self, fake_ctx):
        fake_ctx.add_user("bob")
        result = invoke(fake_ctx, ["users", "delete", "bob"], input="n\n")
        assert result.exit_code != 0
        assert len(fake_ctx.api.users.models) == 1


class TestDatasetsList:
    def test_lists_datasets_with_workspace_names(self, fake_ctx):
        fake_ctx.add_workspace("review")
        fake_ctx.add_dataset("contracts", WS_ID, status="ready")
        fake_ctx.api.datasets.models[-1].workspace_id = fake_ctx.api.workspaces.models[0].id
        result = invoke(fake_ctx, ["--json", "datasets", "list"])
        assert result.exit_code == 0
        rows = json.loads(result.output)
        assert rows[0]["workspace"] == "review"
        assert rows[0]["status"] == "ready"

    def test_lists_datasets_as_table(self, fake_ctx):
        fake_ctx.add_workspace("review")
        fake_ctx.add_dataset("contracts", WS_ID)
        result = invoke(fake_ctx, ["datasets", "list"])
        assert result.exit_code == 0
        assert "contracts" in result.output

    def test_filters_by_workspace(self, fake_ctx):
        ws = fake_ctx.add_workspace("review")
        other = fake_ctx.add_workspace("archive")
        fake_ctx.add_dataset("contracts", ws.id)
        fake_ctx.add_dataset("memos", other.id)
        result = invoke(fake_ctx, ["--json", "datasets", "list", "--workspace", "review"])
        assert result.exit_code == 0
        rows = json.loads(result.output)
        assert [row["name"] for row in rows] == ["contracts"]


class TestDatasetsGet:
    def test_get_dataset_detail(self, fake_ctx):
        ws = fake_ctx.add_workspace("review")
        dataset = fake_ctx.add_dataset("contracts", ws.id, guidelines="Be careful.")
        fake_ctx.add_field(dataset.id, "prompt")
        fake_ctx.add_question(dataset.id, "quality")
        result = invoke(fake_ctx, ["--json", "datasets", "get", "contracts"])
        assert result.exit_code == 0
        row = json.loads(result.output)
        assert row["name"] == "contracts"
        assert row["workspace"] == "review"
        assert row["guidelines"] == "Be careful."
        assert row["fields"][0]["name"] == "prompt"
        assert row["questions"][0]["type"] == "text"

    def test_get_missing_dataset_fails(self, fake_ctx):
        result = invoke(fake_ctx, ["datasets", "get", "ghost"])
        assert result.exit_code == 1
        assert "No dataset named 'ghost'" in result.output

    def test_get_with_wrong_workspace_fails(self, fake_ctx):
        ws = fake_ctx.add_workspace("review")
        fake_ctx.add_workspace("archive")
        fake_ctx.add_dataset("contracts", ws.id)
        result = invoke(fake_ctx, ["datasets", "get", "contracts", "--workspace", "archive"])
        assert result.exit_code == 1
        assert "No dataset named 'contracts' in workspace 'archive'" in result.output

    def test_get_settings_as_json_document(self, fake_ctx):
        ws = fake_ctx.add_workspace("review")
        dataset = fake_ctx.add_dataset("contracts", ws.id)
        fake_ctx.add_field(dataset.id, "prompt")
        result = invoke(fake_ctx, ["datasets", "get", "contracts", "--settings"])
        assert result.exit_code == 0
        document = json.loads(result.output)
        assert document["fields"][0]["name"] == "prompt"
        assert result.output.startswith("{")

    def test_get_settings_as_yaml(self, fake_ctx):
        ws = fake_ctx.add_workspace("review")
        dataset = fake_ctx.add_dataset("contracts", ws.id)
        fake_ctx.add_question(dataset.id, "quality")
        result = invoke(fake_ctx, ["datasets", "get", "contracts", "--settings", "--format", "yaml"])
        assert result.exit_code == 0
        assert "quality" in result.output
        assert not result.output.lstrip().startswith("{")

    def test_get_settings_to_file(self, fake_ctx, tmp_path):
        ws = fake_ctx.add_workspace("review")
        dataset = fake_ctx.add_dataset("contracts", ws.id)
        fake_ctx.add_field(dataset.id, "prompt")
        target = tmp_path / "settings.json"
        result = invoke(fake_ctx, ["--json", "datasets", "get", "contracts", "--settings", "-o", str(target)])
        assert result.exit_code == 0
        document = json.loads(target.read_text())
        assert document["fields"][0]["name"] == "prompt"
        assert json.loads(result.output)["action"] == "dump-settings"
