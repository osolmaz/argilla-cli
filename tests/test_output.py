"""Tests for the --json and table output helpers."""

import json

import click

from argilla_cli.cli import main


class TestEmit:
    def test_json_flag_prints_json(self, runner, isolated_config):
        @main.command("emit-probe-json")
        @click.pass_context
        def _probe(ctx):
            from argilla_cli.output import emit
            emit(ctx, [{"name": "ws1", "count": 2}])

        result = runner.invoke(main, ["--json", "emit-probe-json"])
        assert result.exit_code == 0, result.output
        assert json.loads(result.output) == [{"name": "ws1", "count": 2}]

    def test_table_without_json_flag(self, runner, isolated_config):
        @main.command("emit-probe-table")
        @click.pass_context
        def _probe(ctx):
            from argilla_cli.output import emit
            emit(ctx, [{"name": "ws1", "count": 2}], columns=[("name", "NAME"), ("count", "COUNT")])

        result = runner.invoke(main, ["emit-probe-table"])
        assert result.exit_code == 0, result.output
        lines = result.output.strip().splitlines()
        assert lines[0].split() == ["NAME", "COUNT"]
        assert lines[1].startswith("---")
        assert "ws1" in lines[2] and "2" in lines[2]

    def test_dict_prints_key_value_lines_without_json(self, runner, isolated_config):
        @main.command("emit-probe-dict")
        @click.pass_context
        def _probe(ctx):
            from argilla_cli.output import emit
            emit(ctx, {"username": "amelie", "role": "admin"})

        result = runner.invoke(main, ["emit-probe-dict"])
        assert result.exit_code == 0, result.output
        lines = result.output.strip().splitlines()
        assert lines[0].startswith("username") and lines[0].endswith("amelie")
        assert lines[1].startswith("role") and lines[1].endswith("admin")

    def test_empty_list_says_no_results_without_json(self, runner, isolated_config):
        @main.command("emit-probe-empty")
        @click.pass_context
        def _probe(ctx):
            from argilla_cli.output import emit
            emit(ctx, [])

        result = runner.invoke(main, ["emit-probe-empty"])
        assert result.exit_code == 0, result.output
        assert result.output.strip() == "No results."

    def test_json_empty_list_is_valid_json(self, runner, isolated_config):
        @main.command("emit-probe-empty-json")
        @click.pass_context
        def _probe(ctx):
            from argilla_cli.output import emit
            emit(ctx, [])

        result = runner.invoke(main, ["--json", "emit-probe-empty-json"])
        assert result.exit_code == 0, result.output
        assert json.loads(result.output) == []

    def test_table_rows_can_differ_from_json_data(self, runner, isolated_config):
        @main.command("emit-probe-rows")
        @click.pass_context
        def _probe(ctx):
            from argilla_cli.output import emit
            data = [{"id": "r1", "fields": {"text": "a" * 100}}]
            emit(ctx, data, columns=[("id", "ID")], table=[{"id": "r1"}])

        result = runner.invoke(main, ["emit-probe-rows"])
        assert result.exit_code == 0, result.output
        assert "a" * 100 not in result.output
