"""Read-only smoke test against the Legal Benchmarks studio server.

Doubly opt-in: the `studio` marker AND ARGILLA_CLI_STUDIO=1. Credentials come
from the credentials file at run time; nothing from that file may appear in
the output. This test only reads (whoami, datasets list) and never writes.
"""

import json
import os
from pathlib import Path

import pytest
from tests.helpers import invoke

CREDENTIALS_FILE = Path(
    os.environ.get("ARGILLA_CLI_CREDENTIALS_FILE", "~/offline/secrets/legalbenchmarks_studio_token.env")
)
STUDIO_URL = os.environ.get("ARGILLA_CLI_STUDIO_URL", "https://studio.legalbenchmarks.ai")

pytestmark = [
    pytest.mark.studio,
    pytest.mark.skipif(os.environ.get("ARGILLA_CLI_STUDIO") != "1", reason="needs ARGILLA_CLI_STUDIO=1"),
]


@pytest.fixture()
def secret_values() -> dict[str, str]:
    """Read the file ONCE so the test can assert values stay hidden."""
    values: dict[str, str] = {}
    if CREDENTIALS_FILE.exists():
        for line in CREDENTIALS_FILE.read_text().splitlines():
            key, _, value = line.partition("=")
            if key.strip() and value.strip():
                values[key.strip()] = value.strip().strip("\"'")
    return values


class TestStudioSmoke:
    def test_whoami_reads_the_studio(self, secret_values):
        result = invoke(None, ["--json", "--url", STUDIO_URL, "--credentials-file", str(CREDENTIALS_FILE), "whoami"])
        assert result.exit_code == 0, result.output
        whoami = json.loads(result.stdout)
        assert whoami["username"]
        self._assert_no_secrets(result.output, secret_values)

    def test_datasets_list_reads_the_studio(self, secret_values):
        result = invoke(
            None,
            ["--json", "--url", STUDIO_URL, "--credentials-file", str(CREDENTIALS_FILE), "datasets", "list"],
        )
        assert result.exit_code == 0, result.output
        json.loads(result.stdout)  # a JSON array comes back
        self._assert_no_secrets(result.output, secret_values)

    def _assert_no_secrets(self, output: str, secret_values: dict[str, str]) -> None:
        for key, value in secret_values.items():
            if key.lower() in ("url", "argilla_api_url", "username", "argilla_username"):
                continue  # a URL or username is not a secret; the account name may show
            assert value not in output, f"the value of {key} leaked into the output"
