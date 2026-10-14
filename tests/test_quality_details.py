"""Unit tests for small helpers, added to raise the mutation kill rate."""

import pytest

from argilla_cli.commands.core import _release
from argilla_cli.output import _cell, _print_table, emit
from argilla_cli.profiles import is_write


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
