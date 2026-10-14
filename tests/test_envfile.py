"""Tests for parsing credentials env files."""

from pathlib import Path

from argilla_cli.auth import credentials_from_file, parse_env_file


def test_parse_basic_pairs(tmp_path: Path):
    f = tmp_path / "creds.env"
    f.write_text("USERNAME=amelie\nPASSWORD=sesame\n")
    assert parse_env_file(f) == {"USERNAME": "amelie", "PASSWORD": "sesame"}


def test_parse_export_quotes_comments(tmp_path: Path):
    f = tmp_path / "creds.env"
    f.write_text(
        "# a comment\n"
        "export ARGILLA_API_URL=https://studio.example.org\n"
        'ARGILLA_PASSWORD="sesame seed"\n'
        "ARGILLA_API_KEY='key-123'\n"
        "\n"
        "this line has no equals\n"
    )
    assert parse_env_file(f) == {
        "ARGILLA_API_URL": "https://studio.example.org",
        "ARGILLA_PASSWORD": "sesame seed",
        "ARGILLA_API_KEY": "key-123",
    }


def test_credentials_from_file_bare_names(tmp_path: Path):
    f = tmp_path / "creds.env"
    f.write_text("USERNAME=amelie\nPASSWORD=sesame\n")
    assert credentials_from_file(f) == {"username": "amelie", "password": "sesame"}


def test_credentials_from_file_argilla_names_win(tmp_path: Path):
    f = tmp_path / "creds.env"
    f.write_text(
        "USERNAME=other\n"
        "ARGILLA_USERNAME=amelie\n"
        "PASSWORD=sesame\n"
        "ARGILLA_API_KEY=key-123\n"
    )
    values = credentials_from_file(f)
    assert values["username"] == "amelie"
    assert values["password"] == "sesame"
    assert values["api_key"] == "key-123"
