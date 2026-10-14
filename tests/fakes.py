"""A fake in-memory Argilla client shaped like the official SDK.

Commands and helpers are written against the SDK's resource surface
(``client.workspaces(...)``, ``client.api.workspaces.list()``, ...). These
fakes mirror just that surface, so the command layer is tested with the real
``argilla`` resource classes (``rg.Workspace``, ``rg.User``, ``rg.Dataset``)
on top of an in-memory store - no network.
"""

# Deferred annotations: the classes below define methods named like builtins
# (list, get), and a method annotation must not resolve to that method.
from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

from argilla import Dataset, User, Workspace
from argilla._models import (
    DatasetModel,
    FieldModel,
    QuestionModel,
    RecordModel,
    TextFieldSettings,
    UserModel,
    WorkspaceModel,
)
from argilla._models._record._metadata import MetadataModel


class _FakeResourceAPI:
    """In-memory stand-in for a ResourceAPI subclass."""

    def __init__(self) -> None:
        self.models: list[Any] = []

    def list(self, **_kwargs: Any) -> list[Any]:
        return list(self.models)

    def create(self, model: Any) -> Any:
        model.id = model.id or uuid4()
        self.models.append(model)
        return model

    def get(self, resource_id: UUID) -> Any:
        for model in self.models:
            if model.id == resource_id:
                return model
        return None

    def delete(self, resource_id: UUID) -> None:
        self.models = [model for model in self.models if model.id != resource_id]


class FakeWorkspacesAPI(_FakeResourceAPI):
    pass


class FakeUsersAPI(_FakeResourceAPI):
    def add_to_workspace(self, workspace_id: UUID, user_id: UUID) -> None:
        pass

    def delete_from_workspace(self, workspace_id: UUID, user_id: UUID) -> None:
        pass


class FakeDatasetsAPI(_FakeResourceAPI):
    def update(self, model: Any) -> Any:
        stored = self.get(model.id)
        if stored is None:
            raise KeyError(str(model.id))
        stored.guidelines = model.guidelines
        stored.allow_extra_metadata = model.allow_extra_metadata
        return stored

    def list(self, workspace_id: Any = None) -> list[Any]:
        if not workspace_id:
            return list(self.models)
        return [model for model in self.models if model.workspace_id == workspace_id]

    def exists(self, dataset_id: UUID) -> bool:
        return self.get(dataset_id) is not None

    def publish(self, dataset_id: UUID) -> None:
        pass

    def upsert(self, model: Any) -> Any:
        return self.create(model)


class FakeRecordsAPI:
    """Records, keyed by server id, scoped to datasets (RecordModel has no
    dataset_id field, so the fake tracks the association separately)."""

    MAX_RECORDS_PER_CREATE_BULK = 500
    MAX_RECORDS_PER_UPSERT_BULK = 500
    MAX_RECORDS_PER_DELETE_BULK = 100

    def __init__(self) -> None:
        self.models: list[Any] = []
        self.dataset_ids: dict[UUID, UUID] = {}

    def list(
        self,
        dataset_id: Any = None,
        offset: int = 0,
        limit: int = 100,
        **_kwargs: Any,
    ) -> list[Any]:
        scoped = [model for model in self.models if self.dataset_ids.get(model.id) == dataset_id]
        return scoped[offset : offset + limit]

    def get(self, record_id: UUID) -> Any:
        for model in self.models:
            if model.id == record_id:
                return model
        return None

    def update(self, model: Any) -> Any:
        stored = self.get(model.id)
        if stored is None:
            raise KeyError(str(model.id))
        stored.fields = model.fields
        if isinstance(model.metadata, dict):
            # The server answers with metadata as model rows, not a dict.
            stored.metadata = [MetadataModel(name=name, value=value) for name, value in model.metadata.items()]
        else:
            stored.metadata = model.metadata
        return stored

    def delete(self, record_id: UUID) -> None:
        self.models = [model for model in self.models if model.id != record_id]
        self.dataset_ids.pop(record_id, None)

    def create_record_response(self, record_id: UUID, response: Any) -> None:
        stored = self.get(record_id)
        if stored is None:
            raise KeyError(str(record_id))
        stored.responses = [*list(stored.responses or []), response]

    def bulk_upsert(self, dataset_id: UUID, records: list[Any]) -> tuple[list[Any], int]:
        """Upsert by external_id, mirroring the server's response shape."""
        updated = 0
        result: list[Any] = []
        for incoming in records:
            existing_id = next(
                (
                    m.id
                    for m in self.models
                    if self.dataset_ids.get(m.id) == dataset_id and m.external_id == incoming.external_id
                ),
                None,
            )
            if existing_id is not None:
                stored = self.get(existing_id)
                stored.fields = incoming.fields
                stored.metadata = incoming.metadata
                stored.suggestions = incoming.suggestions
                stored.responses = incoming.responses
                updated += 1
                result.append(stored)
            else:
                incoming.id = incoming.id or uuid4()
                self.models.append(incoming)
                self.dataset_ids[incoming.id] = dataset_id
                result.append(incoming)
        return result, updated


class FakeDatasetRelatedAPI(_FakeResourceAPI):
    """Fields, questions, metadata, and vectors, scoped to one dataset."""

    def list(self, dataset_id: Any = None, **_kwargs: Any) -> list[Any]:
        if dataset_id is None:
            return list(self.models)
        return [model for model in self.models if model.dataset_id == dataset_id]

    def update(self, model: Any) -> Any:
        stored = next((m for m in self.models if m.id == model.id), None)
        if stored is None:
            self.models.append(model)
            return model
        stored.settings = model.settings
        stored.title = model.title
        stored.required = model.required
        return stored


class FakeSettingsAPI:
    def __init__(self, datasets_api: FakeDatasetsAPI) -> None:
        self._datasets = datasets_api

    def create(self, dataset_id: UUID, model: Any) -> Any:
        return model

    def delete(self, dataset_id: UUID) -> None:
        pass


class FakeWorkspacesManager:
    """Mirrors argilla.workspaces.Workspaces: callable lookup + iteration."""

    def __init__(self, client: FakeClient) -> None:
        self._client = client

    def __call__(self, name: str) -> Workspace | None:
        for model in self._client.api.workspaces.models:
            if model.name == name:
                return Workspace.from_model(model, client=self._client)
        return None

    def __iter__(self):  # type: ignore[no-untyped-def]
        return iter([self(name=model.name) for model in self._client.api.workspaces.models])

    @property
    def default(self) -> Workspace | None:
        models = self._client.api.workspaces.models
        return self(name=models[0].name) if models else None


class FakeUsersManager:
    def __init__(self, client: FakeClient) -> None:
        self._client = client

    def __call__(self, username: str) -> User | None:
        for model in self._client.api.users.models:
            if model.username == username:
                return User(_model=model, client=self._client)
        return None

    def __iter__(self):  # type: ignore[no-untyped-def]
        return iter([self(username=model.username) for model in self._client.api.users.models])


class FakeDatasetsManager:
    def __init__(self, client: FakeClient) -> None:
        self._client = client

    def __call__(self, name: str | None = None, workspace: Any | None = None, id: Any | None = None) -> Dataset | None:
        workspace_id = workspace.id if workspace is not None else None
        for model in self._client.api.datasets.models:
            if id is not None and model.id != id:
                continue
            if name is not None and model.name != name:
                continue
            if workspace_id is not None and model.workspace_id != workspace_id:
                continue
            return Dataset.from_model(model, client=self._client)
        return None

    def __iter__(self):  # type: ignore[no-untyped-def]
        return iter([self(name=model.name) for model in self._client.api.datasets.models])


class FakeClient:
    """The client object commands receive; ``.api`` backs real SDK resources.

    Mirrors the SDK's default-client contract: resource factories such as
    ``Field.from_model`` fall back to the last-built client.
    """

    _default_client: FakeClient | None = None

    @classmethod
    def _set_default(cls, client: FakeClient) -> None:
        cls._default_client = client

    @classmethod
    def _get_default(cls) -> FakeClient:
        if cls._default_client is None:
            raise RuntimeError("No default client set; build one first.")
        return cls._default_client

    def __init__(self) -> None:
        self.api_url = "https://fake.example.org"
        self.api = SimpleNamespaceLike()
        self.api.workspaces = FakeWorkspacesAPI()
        self.api.users = FakeUsersAPI()
        self.api.datasets = FakeDatasetsAPI()
        self.api.records = FakeRecordsAPI()
        self.api.datasets.workspaces = self.api.workspaces
        # The SDK's record ingest stamps responses with client.me.id.
        self.me = SimpleNamespace(id=uuid4(), username="admin")
        self.api.fields = FakeDatasetRelatedAPI()
        self.api.questions = FakeDatasetRelatedAPI()
        self.api.metadata = FakeDatasetRelatedAPI()
        self.api.vectors = FakeDatasetRelatedAPI()
        self.api.settings = FakeSettingsAPI(self.api.datasets)
        self.workspaces = FakeWorkspacesManager(self)
        self.users = FakeUsersManager(self)
        self.datasets = FakeDatasetsManager(self)

    def add_workspace(self, name: str, workspace_id: UUID | None = None) -> Workspace:
        model = WorkspaceModel(name=name, id=workspace_id or uuid4())
        self.api.workspaces.models.append(model)
        return Workspace.from_model(model, client=self)

    def add_user(
        self,
        username: str,
        role: str = "admin",
        user_id: UUID | None = None,
        first_name: str | None = None,
        last_name: str | None = None,
    ) -> User:
        model = UserModel(
            id=user_id or uuid4(),
            username=username,
            role=role,
            first_name=first_name,
            last_name=last_name,
        )
        self.api.users.models.append(model)
        return User(_model=model, client=self)

    def add_dataset(
        self,
        name: str,
        workspace_id: UUID,
        dataset_id: UUID | None = None,
        status: str = "ready",
        guidelines: str | None = None,
    ) -> DatasetModel:
        model = DatasetModel(
            id=dataset_id or uuid4(),
            name=name,
            workspace_id=workspace_id,
            status=status,
            guidelines=guidelines,
        )
        self.api.datasets.models.append(model)
        return model

    def add_field(self, dataset_id: UUID, name: str, title: str | None = None, required: bool = True) -> None:
        self.api.fields.models.append(
            FieldModel(
                name=name,
                title=title or name.title(),
                required=required,
                settings=TextFieldSettings(),
                dataset_id=dataset_id,
            )
        )

    def add_question(self, dataset_id: UUID, name: str, question_type: str = "text") -> None:
        self.api.questions.models.append(
            QuestionModel(
                id=uuid4(),
                name=name,
                title=name.title(),
                required=True,
                settings={"type": question_type},
                dataset_id=dataset_id,
            )
        )

    def add_record(
        self,
        dataset_id: UUID,
        external_id: str,
        fields: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        record_id: UUID | None = None,
    ) -> RecordModel:
        model = RecordModel(
            id=record_id or uuid4(),
            external_id=external_id,
            fields=fields if fields is not None else {},
            metadata=metadata or {},
        )
        self.api.records.models.append(model)
        self.api.records.dataset_ids[model.id] = dataset_id
        return model


class SimpleNamespaceLike:
    """Attribute bag standing in for the SDK client's ``api`` namespace.

    Managers the tests configure (workspaces, users, datasets, ...) are set
    explicitly; any other manager the SDK resources touch is auto-stubbed
    with an empty in-memory API so construction never fails.
    """

    def __init__(self) -> None:
        self._stubs: dict[str, Any] = {}

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        stub = self._stubs.setdefault(name, _FakeResourceAPI())
        return stub


__all__ = [
    "FakeClient",
    "FakeDatasetRelatedAPI",
    "FakeWorkspacesAPI",
]
