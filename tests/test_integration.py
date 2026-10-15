"""Integration tests against a real Argilla server stack in Docker.

Opt-in: set ARGILLA_CLI_INTEGRATION=1 (and have Docker with Compose). The
stack follows the official v2.8.0 deployment (server, postgres, elasticsearch,
redis), with the server image pinned by digest. The flow covers login with a
password and with an API key, workspace/dataset creation from a settings
file, record upsert, response submission, export, import, and teardown.
"""

import json
import os
import socket
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from uuid import uuid4

import pytest
from tests.helpers import invoke

# The plan names the quickstart image, but that line stopped at v2.0.0rc2;
# the official server image continues to track releases and has v2.8.0.
IMAGE_TAG = "argilla/argilla-server:v2.8.0"
OWNER_USERNAME = "owner"
OWNER_PASSWORD = f"pw-{uuid4()}"
OWNER_API_KEY = f"key-{uuid4()}"

COMPOSE_TEMPLATE = """
services:
  argilla:
    image: {image}
    ports:
      - "{port}:6900"
    environment:
      ARGILLA_HOME_PATH: /var/lib/argilla
      ARGILLA_ELASTICSEARCH: http://elasticsearch:9200
      ARGILLA_DATABASE_URL: postgresql+asyncpg://postgres:postgres@postgres:5432/argilla
      ARGILLA_REDIS_URL: redis://redis:6379/0
      USERNAME: {username}
      PASSWORD: {password}
      API_KEY: {api_key}
      WORKSPACE: default
    depends_on:
      - postgres
      - elasticsearch
      - redis
  postgres:
    image: postgres:14
    environment:
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: postgres
      POSTGRES_DB: argilla
  elasticsearch:
    image: docker.elastic.co/elasticsearch/elasticsearch:8.17.0
    environment:
      - ES_JAVA_OPTS=-Xms512m -Xmx512m -XX:UseSVE=0
      - discovery.type=single-node
      - xpack.security.enabled=false
      - cluster.routing.allocation.disk.threshold_enabled=false
  redis:
    image: redis
"""

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.environ.get("ARGILLA_CLI_INTEGRATION") != "1", reason="needs ARGILLA_CLI_INTEGRATION=1"),
]

SETTINGS_DOC = {
    "fields": [{"name": "prompt", "title": "Prompt", "required": True, "settings": {"type": "text"}}],
    "questions": [{"name": "quality", "title": "Quality", "required": True, "settings": {"type": "text"}}],
}


def compose(project: Path, *args: str, timeout: int = 900) -> str:
    result = subprocess.run(
        ["docker", "compose", "-p", "argilla-cli-it", "-f", str(project / "compose.yaml"), *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=True,
    )
    return result.stdout.strip()


def ready(url: str) -> bool:
    try:
        with urllib.request.urlopen(f"{url}/api/v1/version", timeout=5) as response:
            return response.status == 200
    except OSError:
        return False


def bootstrap_user_ready(url: str) -> bool:
    """The server answers AND the bootstrap user can draw a token."""
    if not ready(url):
        return False
    data = urllib.parse.urlencode({"username": OWNER_USERNAME, "password": OWNER_PASSWORD}).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(f"{url}/api/v1/token", data=data), timeout=5) as response:
            return response.status in (200, 201)
    except (OSError, urllib.error.URLError):
        return False


def free_port() -> int:
    with socket.socket() as handle:
        handle.bind(("127.0.0.1", 0))
        return int(handle.getsockname()[1])


@pytest.fixture(scope="session")
def server_url(tmp_path_factory) -> str:
    """Start the stack, wait until the server answers, then tear it down."""
    subprocess.run(["docker", "compose", "version"], capture_output=True, check=True)
    project = tmp_path_factory.mktemp("argilla-it")
    port = free_port()
    image_ref = subprocess.run(
        ["docker", "image", "inspect", IMAGE_TAG], capture_output=True, text=True, timeout=120, check=True
    )
    (project / "compose.yaml").write_text(
        COMPOSE_TEMPLATE.format(
            image=IMAGE_TAG,
            port=port,
            username=OWNER_USERNAME,
            password=OWNER_PASSWORD,
            api_key=OWNER_API_KEY,
        )
    )
    digest = json.loads(image_ref.stdout)[0]["RepoDigests"][0]
    assert digest.startswith("argilla/argilla-server@sha256:"), digest
    # Run the digest ref, not the tag.
    compose_file = (project / "compose.yaml").read_text().replace(f"image: {IMAGE_TAG}", f"image: {digest}")
    (project / "compose.yaml").write_text(compose_file)

    try:
        compose(project, "up", "-d")
        url = f"http://localhost:{port}"
        deadline = time.time() + 300
        while not bootstrap_user_ready(url):
            if time.time() > deadline:
                tail = compose(project, "logs", "--tail=40")
                raise AssertionError(f"the Argilla stack did not become ready in 300s: {tail}")
            time.sleep(3)
        yield url
    finally:
        compose(project, "down", "-v", timeout=300)


@pytest.fixture()
def flow(tmp_path, server_url, isolated_config):
    """A real server, an isolated config, and a settings file."""
    settings_file = tmp_path / "settings.json"
    settings_file.write_text(json.dumps(SETTINGS_DOC))
    return {"url": server_url, "settings": settings_file, "tmp": tmp_path}


class TestFullFlow:
    def test_login_with_password_then_full_flow(self, flow):
        url = flow["url"]
        # The server briefly rejects logins while its user index settles.
        result = None
        for _attempt in range(5):
            result = invoke(
                None,
                ["login", "--url", url, "--username", OWNER_USERNAME, "--password", OWNER_PASSWORD],
            )
            if result.exit_code == 0:
                break
            time.sleep(3)
        assert result.exit_code == 0, result.output

        result = invoke(None, ["--json", "whoami"])
        assert result.exit_code == 0, result.output
        whoami = json.loads(result.stdout)
        assert whoami["username"] == OWNER_USERNAME
        assert whoami["method"] == "password"

        result = invoke(None, ["--json", "workspaces", "create", "it-ws"])
        assert result.exit_code == 0, result.output

        result = invoke(
            None,
            ["--json", "datasets", "create", "it-ds", "--workspace", "it-ws", "--settings", str(flow["settings"])],
        )
        assert result.exit_code == 0, result.output

        records_file = flow["tmp"] / "records.jsonl"
        records_file.write_text(
            json.dumps({"id": "r1", "prompt": "first"}) + "\n" + json.dumps({"id": "r2", "prompt": "second"}) + "\n"
        )
        result = invoke(None, ["--json", "records", "add", "it-ds", "--file", str(records_file)])
        assert result.exit_code == 0, result.output

        # Rerun the same file: the upsert must not duplicate.
        result = invoke(None, ["--json", "records", "add", "it-ds", "--file", str(records_file)])
        assert result.exit_code == 0, result.output
        result = invoke(None, ["--json", "records", "list", "it-ds"])
        assert len(json.loads(result.stdout)) == 2

        result = invoke(
            None,
            ["--json", "responses", "submit", "it-ds", "r1", "--answers", '{"quality": "ok"}'],
        )
        assert result.exit_code == 0, result.output

        suggestions_file = flow["tmp"] / "suggestions.jsonl"
        suggestions_file.write_text(json.dumps({"id": "r1", "quality": "good"}) + "\n")
        result = invoke(None, ["--json", "suggestions", "add", "it-ds", "--file", str(suggestions_file)])
        assert result.exit_code == 0, result.output

        # Submit as the caller (the 2.8 server pins responses to the
        # authenticated user; there is no filing for someone else) and
        # check the response really landed under that name.
        result = invoke(None, ["--json", "responses", "submit", "it-ds", "r2", "--answers", '{"quality": "fine"}'])
        assert result.exit_code == 0, result.output
        result = invoke(None, ["--json", "responses", "list", "it-ds", "--user", OWNER_USERNAME])
        rows = json.loads(result.stdout)
        assert rows, "the response did not land under the submitting user"
        assert all(row["user"] == OWNER_USERNAME for row in rows)

        export_file = flow["tmp"] / "export.jsonl"
        result = invoke(None, ["--json", "datasets", "export", "it-ds", "-o", str(export_file)])
        assert result.exit_code == 0, result.output
        lines = [json.loads(line) for line in export_file.read_text().splitlines()]
        assert len(lines) == 2
        first = next(line for line in lines if line["id"] == "r1")
        assert first["prompt"] == "first"
        assert first["quality"] == "good"  # the suggestion, flat
        assert first["responses"][0]["value"] == "ok"
        assert first["responses"][0]["user"] == OWNER_USERNAME

    def test_login_with_api_key(self, flow):
        from argilla_cli import auth

        url = flow["url"]
        user = auth.login_with_password(url, OWNER_USERNAME, OWNER_PASSWORD)
        assert user["username"] == OWNER_USERNAME
        api_key = user["api_key"]
        assert api_key

        result = invoke(None, ["login", "--url", url, "--api-key", api_key])
        assert result.exit_code == 0, result.output
        result = invoke(None, ["--json", "whoami"])
        assert json.loads(result.stdout)["method"] == "api_key"

        # The key must never appear in the output.
        result = invoke(None, ["--json", "config", "show"])
        assert result.exit_code == 0, result.output
        assert api_key not in result.output
        result = invoke(None, ["--json", "whoami"])
        assert api_key not in result.output

    def test_exported_file_imports_into_a_fresh_dataset(self, flow):
        url = flow["url"]
        invoke(None, ["login", "--url", url, "--username", OWNER_USERNAME, "--password", OWNER_PASSWORD])
        invoke(None, ["--json", "datasets", "create", "import-ds", "--settings", str(flow["settings"])])

        records_file = flow["tmp"] / "records.jsonl"
        records_file.write_text(json.dumps({"id": "x1", "prompt": "hello"}) + "\n")
        invoke(None, ["--json", "records", "add", "import-ds", "--file", str(records_file)])

        export_file = flow["tmp"] / "export.jsonl"
        invoke(None, ["--json", "datasets", "export", "import-ds", "-o", str(export_file)])

        invoke(None, ["--json", "datasets", "create", "import-ds-2", "--settings", str(flow["settings"])])
        result = invoke(None, ["--json", "datasets", "import", "import-ds-2", str(export_file)])
        assert result.exit_code == 0, result.output
        result = invoke(None, ["--json", "records", "list", "import-ds-2"])
        rows = json.loads(result.stdout)
        assert len(rows) == 1
        assert rows[0]["fields"]["prompt"] == "hello"

    def test_settings_dump_reapplies_without_touching_the_source(self, flow):
        """The dump-and-reuse workflow must never write back to the source."""
        url = flow["url"]
        invoke(None, ["login", "--url", url, "--username", OWNER_USERNAME, "--password", OWNER_PASSWORD])
        invoke(None, ["--json", "datasets", "create", "dump-src", "--settings", str(flow["settings"])])
        invoke(None, ["--json", "datasets", "create", "dump-dst", "--settings", str(flow["settings"])])

        dump_file = flow["tmp"] / "dump.json"
        result = invoke(None, ["--json", "datasets", "get", "dump-src", "--settings", "-o", str(dump_file)])
        assert result.exit_code == 0, result.output
        document = json.loads(dump_file.read_text())
        assert all("id" not in item for item in document["fields"])

        result = invoke(None, ["--json", "datasets", "update", "dump-dst", "--settings", str(dump_file)])
        assert result.exit_code == 0, result.output

        result = invoke(None, ["--json", "datasets", "get", "dump-src"])
        assert result.exit_code == 0, result.output
        source = json.loads(result.stdout)
        assert [field["name"] for field in source["fields"]] == ["prompt"]
        assert [question["name"] for question in source["questions"]] == ["quality"]
