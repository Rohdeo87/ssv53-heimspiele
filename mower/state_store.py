from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Protocol

from mower.state import AutomationState


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
