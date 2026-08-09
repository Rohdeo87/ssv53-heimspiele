from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Mapping, Protocol

from azure.core import MatchConditions
from azure.core.exceptions import (
    HttpResponseError,
    ResourceExistsError,
    ResourceNotFoundError,
)
from azure.data.tables import TableClient, UpdateMode
from azure.identity import ManagedIdentityCredential

from mower.state import AutomationState, STATE_KEY


class StateConflictError(RuntimeError):
    pass


class StateStore(Protocol):
    def load(self) -> AutomationState:
        ...

    def save(
        self,
        state: AutomationState,
        *,
        expected_revision: int,
    ) -> None:
        ...


class InMemoryStateStore:
    def __init__(self, initial: AutomationState | None = None) -> None:
        self._state = initial or AutomationState()

    def load(self) -> AutomationState:
        return AutomationState.from_mapping(self._state.to_dict())

    def save(
        self,
        state: AutomationState,
        *,
        expected_revision: int,
    ) -> None:
        if self._state.revision != expected_revision:
            raise StateConflictError(
                "Der Zustand wurde parallel verändert."
            )
        if state.revision <= expected_revision:
            raise ValueError(
                "Der neue Zustand muss eine höhere Revision besitzen."
            )
        self._state = AutomationState.from_mapping(state.to_dict())


class JsonFileStateStore:
    """Lokaler Test-Store; Azure Table Storage folgt in einer späteren Phase."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def load(self) -> AutomationState:
        if not self.path.exists():
            return AutomationState()
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("Die Zustandsdatei muss ein JSON-Objekt enthalten.")
        return AutomationState.from_mapping(data)

    def save(
        self,
        state: AutomationState,
        *,
        expected_revision: int,
    ) -> None:
        current = self.load()
        if current.revision != expected_revision:
            raise StateConflictError(
                "Der Zustand wurde parallel verändert."
            )
        if state.revision <= expected_revision:
            raise ValueError(
                "Der neue Zustand muss eine höhere Revision besitzen."
            )

        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            state.to_dict(),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ) + "\n"
        fd, temporary_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.",
            suffix=".tmp",
            dir=str(self.path.parent),
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_name, self.path)
        finally:
            if os.path.exists(temporary_name):
                os.unlink(temporary_name)

class AzureTableStateStore:
    """Produktiver Zustands-Store über Azure Table Storage und Managed Identity."""

    PARTITION_KEY = "ssv53-mower"

    def __init__(self, table_client: TableClient) -> None:
        self._table_client = table_client
        self._etag: str | None = None
        self._loaded_revision: int | None = None

    @classmethod
    def from_environment(
        cls,
        values: Mapping[str, str],
        *,
        credential_factory=ManagedIdentityCredential,
        table_client_factory=TableClient,
    ) -> "AzureTableStateStore":
        endpoint = str(values.get("SSV53_STORAGE_ACCOUNT_URL", "")).strip()
        table_name = str(values.get("SSV53_STATE_TABLE_NAME", "")).strip()
        client_id = str(
            values.get("SSV53_STATE_MANAGED_IDENTITY_CLIENT_ID")
            or values.get("AzureWebJobsStorage__clientId")
            or ""
        ).strip()
        if not endpoint or not table_name or not client_id:
            raise RuntimeError(
                "Azure-State-Store ist unvollständig konfiguriert "
                "(SSV53_STORAGE_ACCOUNT_URL, SSV53_STATE_TABLE_NAME, Managed-Identity-Client-ID)."
            )
        credential = credential_factory(client_id=client_id)
        client = table_client_factory(
            endpoint=endpoint,
            table_name=table_name,
            credential=credential,
        )
        return cls(client)

    def load(self) -> AutomationState:
        try:
            entity = self._table_client.get_entity(
                partition_key=self.PARTITION_KEY,
                row_key=STATE_KEY,
            )
        except ResourceNotFoundError:
            self._etag = None
            self._loaded_revision = 0
            return AutomationState()

        state = AutomationState.from_mapping(entity)
        metadata = getattr(entity, "metadata", {}) or {}
        self._etag = str(metadata.get("etag") or "").strip() or None
        self._loaded_revision = state.revision
        return state

    def save(
        self,
        state: AutomationState,
        *,
        expected_revision: int,
    ) -> None:
        if self._loaded_revision != expected_revision:
            raise StateConflictError("Zustand wurde nicht mit der erwarteten Revision geladen.")
        if state.revision <= expected_revision:
            raise ValueError("Der neue Zustand muss eine höhere Revision besitzen.")

        entity = {
            "PartitionKey": self.PARTITION_KEY,
            "RowKey": STATE_KEY,
            **{key: value for key, value in state.to_dict().items() if value is not None},
        }

        if self._etag is None and expected_revision == 0:
            try:
                self._table_client.create_entity(entity=entity)
            except ResourceExistsError as exc:
                raise StateConflictError("Zustand wurde parallel angelegt.") from exc
            self._loaded_revision = state.revision
            return

        if not self._etag:
            raise StateConflictError("ETag für optimistische Zustandsprüfung fehlt.")

        try:
            self._table_client.update_entity(
                entity=entity,
                mode=UpdateMode.REPLACE,
                etag=self._etag,
                match_condition=MatchConditions.IfNotModified,
            )
        except HttpResponseError as exc:
            if getattr(exc, "status_code", None) in {409, 412}:
                raise StateConflictError("Zustand wurde parallel verändert.") from exc
            raise
        self._loaded_revision = state.revision

