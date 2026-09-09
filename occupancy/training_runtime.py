"""One manifest-bound training source for App and mower; default OFF.

The envelope is embedded identically in the existing mower configuration and
structured match file. The runtime manifest already hashes both files. This
adds no network request. Only trusted server configuration selects the mode.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

from occupancy.training_calendar import (
    TrainingBatch, TrainingCancellation, content_digest, legacy_source_hashes,
    resolve_training_calendar, validate_batch_for_range, validate_calendar,
)

ENVELOPE_KEY = "shared_training_calendar"
OCCUPANCY_KEYS = ("timezone", "effective_from", "effective_to", "resources", "seasons", "cancelled_occurrences")
MOWER_KEYS = ("timezone", "training")


class TrainingSourceUnavailable(RuntimeError):
    def __init__(self) -> None:
        super().__init__("Trainingstermine sind gerade nicht verlässlich verfügbar. Bitte später erneut versuchen; den Platz bis zur Klärung nicht freigeben.")


def training_mode(environment: Mapping[str, str]) -> str:
    return str(environment.get("SHARED_TRAINING_MODE", "OFF")).strip().upper()


def make_training_envelope(document: Mapping[str, Any], *, occupancy_config: Mapping[str, Any],
                           mower_config: Mapping[str, Any], now_utc: datetime) -> dict[str, Any]:
    validation = validate_calendar(document, now_utc=now_utc,
                                   occupancy_config=occupancy_config, mower_config=mower_config)
    if validation.errors:
        raise ValueError("Ungültiger gemeinsamer Trainingskalender: " + "; ".join(validation.errors))
    if document["legacy_sources"] != legacy_source_hashes(occupancy_config, mower_config):
        raise ValueError("Der Trainingskalender gehört nicht zu den angegebenen Ausgangsdaten.")
    content = {
        "schema_version": 1, "calendar": dict(document),
        "occupancy_baseline": {key: occupancy_config.get(key) for key in OCCUPANCY_KEYS},
        "mower_baseline": {key: mower_config.get(key) for key in MOWER_KEYS},
    }
    # Detach mutable caller data and reject non-JSON/NaN values.
    return json.loads(json.dumps({**content, "sha256": content_digest(content)}, allow_nan=False))


@dataclass(frozen=True)
class RuntimeTraining:
    mode: str
    batch: TrainingBatch | None = None
    candidate: TrainingBatch | None = None
    blockers: tuple[str, ...] = ()
    envelope_sha256: str | None = None

    @property
    def blocking_required(self) -> bool:
        return self.mode not in {"OFF", "SHADOW"} and self.batch is None

    def require_available(self) -> None:
        if self.blocking_required:
            raise TrainingSourceUnavailable()

    def metadata(self) -> dict[str, Any]:
        selected = self.batch or self.candidate
        return {
            "mode": self.mode, "active": self.batch is not None,
            "candidate_available": self.candidate is not None,
            "fail_closed": self.blocking_required, "blockers": list(self.blockers),
            "envelope_sha256": self.envelope_sha256,
            "calendar_revision": selected.revision if selected else None,
            "calendar_sha256": selected.content_sha256 if selected else None,
        }


def resolve_runtime_training(
    holder: Mapping[str, Any], *, consumer: str, environment: Mapping[str, str],
    legacy_config: Mapping[str, Any], range_start: datetime, range_end: datetime,
    now_utc: datetime, cancellations: Iterable[Any] = (),
    relocated_keys: Iterable[tuple[str, str]] = (), source_fresh: bool = True,
) -> RuntimeTraining:
    mode = training_mode(environment)
    if mode == "OFF":
        return RuntimeTraining(mode)
    if mode not in {"SHADOW", "ACTIVE"}:
        return RuntimeTraining(mode, blockers=("SHARED_TRAINING_MODE_INVALID",))
    digest = None
    try:
        if consumer not in {"occupancy", "mower"}:
            raise ValueError("TRAINING_CONSUMER_INVALID")
        if not source_fresh:
            raise ValueError("TRAINING_PUBLICATION_STALE")
        envelope = holder.get(ENVELOPE_KEY)
        if not isinstance(envelope, dict) or set(envelope) != {"schema_version", "calendar", "occupancy_baseline", "mower_baseline", "sha256"}:
            raise ValueError("TRAINING_ENVELOPE_MISSING_OR_INVALID")
        if type(envelope["schema_version"]) is not int or envelope["schema_version"] != 1:
            raise ValueError("TRAINING_ENVELOPE_VERSION_INVALID")
        digest = content_digest({key: value for key, value in envelope.items() if key != "sha256"})
        if digest != envelope["sha256"]:
            raise ValueError("TRAINING_ENVELOPE_HASH_MISMATCH")
        occupancy = envelope["occupancy_baseline"]
        mower = envelope["mower_baseline"]
        actual = legacy_source_hashes(legacy_config if consumer == "occupancy" else occupancy,
                                      legacy_config if consumer == "mower" else mower)
        if actual != legacy_source_hashes(occupancy, mower):
            raise ValueError("TRAINING_CONSUMER_BASELINE_CHANGED")
        trusted_cancellations = tuple(
            item if isinstance(item, TrainingCancellation) else TrainingCancellation(
                schedule_id=item.schedule_id, day=item.day,
                requested_at_utc=item.cancelled_at_utc,
                release_not_before_utc=item.release_not_before_utc,
            ) for item in cancellations
        )
        result = resolve_training_calendar(envelope["calendar"], range_start=range_start,
            range_end=range_end, now_utc=now_utc, occupancy_config=occupancy,
            mower_config=mower, cancellations=trusted_cancellations)
        if result.batch is None:
            return RuntimeTraining(mode, blockers=result.validation.errors + result.validation.activation_blockers,
                                   envelope_sha256=digest)
        batch = result.batch
        # Only authenticated, persisted relocation records may suppress originals.
        relocated = set(relocated_keys)
        if relocated:
            batch = replace(batch, events=tuple(event for event in batch.events
                if (event["scheduleId"], datetime.fromisoformat(event["start"]).date().isoformat()) not in relocated))
        validate_batch_for_range(batch, range_start, range_end)
        return RuntimeTraining(mode, batch=batch if mode == "ACTIVE" else None,
                               candidate=batch, envelope_sha256=digest)
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError) as exc:
        return RuntimeTraining(mode, blockers=(str(exc),), envelope_sha256=digest)


def resolve_training_file(path: str | Path, **kwargs: Any) -> RuntimeTraining:
    if training_mode(kwargs["environment"]) == "OFF":
        return RuntimeTraining("OFF")
    try:
        holder = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(holder, dict):
            raise ValueError("TRAINING_ENVELOPE_MISSING_OR_INVALID")
    except (OSError, ValueError):
        return RuntimeTraining(training_mode(kwargs["environment"]), blockers=("TRAINING_SOURCE_UNAVAILABLE",))
    return resolve_runtime_training(holder, **kwargs)
