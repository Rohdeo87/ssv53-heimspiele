from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

from azure.data.tables import TableClient, UpdateMode
from azure.identity import ManagedIdentityCredential

from mower.runtime import CycleResult


PARTITION_PREFIX = "ssv53-irrigation-"


def _as_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _int_list(value: Any) -> list[int]:
    if not isinstance(value, (list, tuple, set)):
        return []
    result: list[int] = []
    for item in value:
        try:
            result.append(int(item))
        except (TypeError, ValueError):
            continue
    return sorted(set(result))


def _table_client(
    values: Mapping[str, str],
    *,
    credential_factory=ManagedIdentityCredential,
    table_client_factory=TableClient,
) -> TableClient:
    endpoint = str(values.get("SSV53_STORAGE_ACCOUNT_URL", "")).strip()
    table_name = str(values.get("SSV53_STATE_TABLE_NAME", "")).strip()
    client_id = str(
        values.get("SSV53_STATE_MANAGED_IDENTITY_CLIENT_ID")
        or values.get("AzureWebJobsStorage__clientId")
        or ""
    ).strip()
    if not endpoint or not table_name or not client_id:
        raise RuntimeError("Beregnungsjournal ist nicht vollständig konfiguriert.")
    return table_client_factory(
        endpoint=endpoint,
        table_name=table_name,
        credential=credential_factory(client_id=client_id),
    )


def observation_entity(result: CycleResult) -> dict[str, Any]:
    """Erzeugt einen idempotenten, minutenweisen Hydrawise-Nachweis.

    Wiederholungen desselben Timer-Zyklus überschreiben nur dieselbe Minute.
    Die Steuerentscheidung selbst wird nicht beeinflusst.
    """

    observed = datetime.fromisoformat(result.executed_at_utc.replace("Z", "+00:00"))
    observed = observed.astimezone(timezone.utc)
    minute = observed.replace(second=0, microsecond=0)
    details = _as_dict(result.details)
    hydrawise = _as_dict(details.get("hydrawise"))
    safety = _as_dict(hydrawise.get("safety"))
    state = _as_dict(details.get("automation_state"))
    return {
        "PartitionKey": PARTITION_PREFIX + minute.strftime("%Y%m%d"),
        "RowKey": minute.strftime("%Y%m%dT%H%MZ"),
        "observed_utc": observed.isoformat(),
        "decision_code": str(result.decision_code or ""),
        "active_relay_ids": json.dumps(
            _int_list(safety.get("active_relay_ids")), separators=(",", ":")
        ),
        "irrigation_plan_id": str(state.get("irrigation_plan_id") or ""),
        "irrigation_phase": str(state.get("irrigation_phase") or ""),
        "irrigation_completed_utc": str(state.get("irrigation_completed_utc") or ""),
        "completed_relay_ids": json.dumps(
            _int_list(state.get("irrigation_completed_relay_ids")),
            separators=(",", ":"),
        ),
        "operator_request_id": str(state.get("operator_request_id") or ""),
        "operator_request_action": str(state.get("operator_request_action") or ""),
        "operator_request_status": str(state.get("operator_request_status") or ""),
        "hydrawise_available": bool(safety.get("available")),
        "hydrawise_fresh": bool(safety.get("fresh")),
        "schema_version": 1,
    }


def record_irrigation_observation(
    result: CycleResult,
    values: Mapping[str, str],
    *,
    table_client: TableClient | Any | None = None,
) -> None:
    client = table_client or _table_client(values)
    client.upsert_entity(
        entity=observation_entity(result),
        mode=UpdateMode.REPLACE,
        timeout=5,
    )


def read_irrigation_observations(
    values: Mapping[str, str],
    period_start_utc: datetime,
    period_end_utc: datetime,
    *,
    table_client: TableClient | Any | None = None,
) -> list[dict[str, Any]]:
    client = table_client or _table_client(values)
    day = period_start_utc.astimezone(timezone.utc).date()
    last_day = period_end_utc.astimezone(timezone.utc).date()
    rows: list[dict[str, Any]] = []
    while day <= last_day:
        partition = PARTITION_PREFIX + day.strftime("%Y%m%d")
        for entity in client.query_entities(
            query_filter="PartitionKey eq @partition",
            parameters={"partition": partition},
        ):
            observed = str(entity.get("observed_utc") or "")
            try:
                timestamp = datetime.fromisoformat(observed.replace("Z", "+00:00"))
            except ValueError:
                continue
            if period_start_utc <= timestamp <= period_end_utc:
                rows.append(
                    {
                        "timestamp": timestamp.isoformat(),
                        "decision_code": entity.get("decision_code"),
                        "active_relay_ids": entity.get("active_relay_ids"),
                        "irrigation_plan_id": entity.get("irrigation_plan_id"),
                        "irrigation_phase": entity.get("irrigation_phase"),
                        "irrigation_completed_utc": entity.get("irrigation_completed_utc"),
                        "completed_relay_ids": entity.get("completed_relay_ids"),
                        "operator_request_id": entity.get("operator_request_id"),
                        "operator_request_action": entity.get("operator_request_action"),
                        "operator_request_status": entity.get("operator_request_status"),
                        "journal_source": True,
                    }
                )
        day += timedelta(days=1)
    return rows
