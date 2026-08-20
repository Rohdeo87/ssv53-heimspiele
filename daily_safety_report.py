from __future__ import annotations

import html
import json
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from email.message import EmailMessage
from email.utils import formataddr
from typing import Any, Callable, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError
from azure.data.tables import TableClient, UpdateMode
from azure.identity import ManagedIdentityCredential

from order_mail import (
    APP_BORDER,
    APP_GOLD,
    APP_LOGO_URL,
    APP_MUTED,
    EMAIL_PATTERN,
    OrderMailSettings,
    _open_authenticated_smtp,
)


REPORT_PARTITION = "ssv53-daily-safety-report-v1"
REPORT_TIME_ZONE = ZoneInfo("Europe/Berlin")
MOWING_ACTIVITIES = frozenset({"MOWING", "LEAVING"})
QUERY_SCOPE = "https://api.applicationinsights.io/.default"
MAX_QUERY_ROWS = 20_000


class DailyReportError(RuntimeError):
    pass


@dataclass(frozen=True)
class CycleObservation:
    timestamp_utc: datetime
    activity: str
    mower_state: str
    error_code: int
    battery_percent: int
    decision_code: str
    command_sent: bool
    hydrawise_available: bool
    hydrawise_fresh: bool
    hydrawise_active_zones: int
    next_irrigation_start_utc: datetime | None
    blocked_source: str
    parking_source: str
    work_area_type: str
    work_area_progress: int
    work_area_last_completed: int


@dataclass(frozen=True)
class DailyReportSummary:
    report_date: date
    period_start_local: datetime
    period_end_local: datetime
    cycle_count_24h: int
    expected_cycles_24h: int
    gap_count_24h: int
    exception_count_24h: int
    command_count_24h: int
    mowing_minutes_7d: int
    average_daily_mowing_minutes_7d: int
    completed_area_cycles_7d: int
    current_work_area_progress: int
    last_completed_area_utc: datetime | None
    daily_mowing_minutes: tuple[tuple[date, int], ...]
    current_activity: str
    current_state: str
    current_error_code: int
    current_battery_percent: int
    hydrawise_available: bool
    hydrawise_fresh: bool
    hydrawise_active_zones: int
    next_irrigation_start_utc: datetime | None
    blocked_source: str
    parking_source: str
    overall_status: str
    warnings: tuple[str, ...]


def _parse_utc(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise DailyReportError("Telemetriezeit enthält keine Zeitzone.")
    return parsed.astimezone(timezone.utc)


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() == "true"


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def report_recipient(values: Mapping[str, str]) -> str:
    recipient = str(values.get("SSV53_DAILY_REPORT_RECIPIENT", "")).strip().lower()
    if not EMAIL_PATTERN.fullmatch(recipient):
        raise DailyReportError("Die Empfängeradresse des Tagesberichts ist ungültig.")
    return recipient


def enabled(values: Mapping[str, str]) -> bool:
    return str(values.get("SSV53_DAILY_REPORT_ENABLED", "")).strip().lower() == "true"


def should_attempt_delivery(now_utc: datetime) -> bool:
    local = now_utc.astimezone(REPORT_TIME_ZONE)
    return 7 <= local.hour <= 9


class AzureDailyReportStore:
    def __init__(self, table_client: TableClient) -> None:
        self._table = table_client

    @classmethod
    def from_environment(
        cls,
        values: Mapping[str, str],
        *,
        credential_factory=ManagedIdentityCredential,
        table_client_factory=TableClient,
    ) -> "AzureDailyReportStore":
        endpoint = str(values.get("SSV53_STORAGE_ACCOUNT_URL", "")).strip()
        table_name = str(values.get("SSV53_STATE_TABLE_NAME", "")).strip()
        client_id = str(
            values.get("SSV53_STATE_MANAGED_IDENTITY_CLIENT_ID")
            or values.get("AzureWebJobsStorage__clientId")
            or ""
        ).strip()
        if not endpoint or not table_name or not client_id:
            raise DailyReportError("Der Zustandspeicher des Tagesberichts ist unvollständig.")
        return cls(
            table_client_factory(
                endpoint=endpoint,
                table_name=table_name,
                credential=credential_factory(client_id=client_id),
            )
        )

    def claim(self, report_date: date, now_utc: datetime) -> bool:
        row_key = report_date.isoformat()
        try:
            self._table.create_entity(
                {
                    "PartitionKey": REPORT_PARTITION,
                    "RowKey": row_key,
                    "Status": "sending",
                    "CreatedAtUtc": now_utc.isoformat(),
                }
            )
            return True
        except ResourceExistsError:
            try:
                current = self._table.get_entity(REPORT_PARTITION, row_key)
            except ResourceNotFoundError:
                return False
            if str(current.get("Status") or "").lower() != "failed":
                return False
            try:
                updated = datetime.fromisoformat(
                    str(current.get("UpdatedAtUtc") or current.get("CreatedAtUtc") or "")
                )
            except ValueError:
                return False
            if updated.tzinfo is None or now_utc - updated < timedelta(minutes=30):
                return False
            self._table.update_entity(
                {
                    "PartitionKey": REPORT_PARTITION,
                    "RowKey": row_key,
                    "Status": "sending",
                    "UpdatedAtUtc": now_utc.isoformat(),
                },
                mode=UpdateMode.MERGE,
            )
            return True

    def mark(self, report_date: date, status: str, now_utc: datetime) -> None:
        self._table.update_entity(
            {
                "PartitionKey": REPORT_PARTITION,
                "RowKey": report_date.isoformat(),
                "Status": status,
                "UpdatedAtUtc": now_utc.isoformat(),
            },
            mode=UpdateMode.MERGE,
        )


class ApplicationInsightsQueryClient:
    def __init__(self, app_id: str, credential: Any) -> None:
        normalized = app_id.strip()
        if not re.fullmatch(r"[0-9a-fA-F-]{36}", normalized):
            raise DailyReportError("Die Application-Insights-App-ID ist ungültig.")
        self._app_id = normalized
        self._credential = credential

    @classmethod
    def from_environment(
        cls,
        values: Mapping[str, str],
        *,
        credential_factory=ManagedIdentityCredential,
    ) -> "ApplicationInsightsQueryClient":
        app_id = str(values.get("SSV53_APP_INSIGHTS_APP_ID", ""))
        client_id = str(
            values.get("SSV53_STATE_MANAGED_IDENTITY_CLIENT_ID")
            or values.get("AzureWebJobsStorage__clientId")
            or ""
        ).strip()
        if not client_id:
            raise DailyReportError("Die Managed Identity für den Tagesbericht fehlt.")
        return cls(app_id, credential_factory(client_id=client_id))

    def execute(self, query: str, *, timespan: str = "P8D") -> list[dict[str, Any]]:
        token = self._credential.get_token(QUERY_SCOPE).token
        body = json.dumps({"query": query, "timespan": timespan}).encode("utf-8")
        request = Request(
            f"https://api.applicationinsights.io/v1/apps/{self._app_id}/query",
            data=body,
            method="POST",
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "User-Agent": "SSV53-Daily-Safety-Report/1.0",
            },
        )
        try:
            with urlopen(request, timeout=45) as response:  # noqa: S310
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            raise DailyReportError(
                f"Application Insights antwortete mit HTTP {exc.code}: {detail}"
            ) from exc
        except (URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise DailyReportError("Application Insights konnte nicht gelesen werden.") from exc
        tables = payload.get("tables") if isinstance(payload, dict) else None
        if not isinstance(tables, list) or not tables:
            raise DailyReportError("Application Insights lieferte keine Tabelle.")
        table = tables[0]
        columns = [str(item.get("name") or "") for item in table.get("columns", [])]
        rows = table.get("rows", [])
        if not columns or not isinstance(rows, list):
            raise DailyReportError("Application Insights lieferte ein ungültiges Tabellenformat.")
        if len(rows) > MAX_QUERY_ROWS:
            raise DailyReportError("Application Insights lieferte unerwartet viele Zeilen.")
        return [dict(zip(columns, row, strict=False)) for row in rows]


def _cycle_query(period_start_utc: datetime, period_end_utc: datetime) -> str:
    start = period_start_utc.isoformat().replace("+00:00", "Z")
    end = period_end_utc.isoformat().replace("+00:00", "Z")
    return f"""
traces
| where timestamp between (datetime({start}) .. datetime({end}))
| where message startswith "SSV53_CONTROL_CYCLE "
| extend p=parse_json(replace_string(message, "SSV53_CONTROL_CYCLE ", ""))
| project timestamp,
    activity=tostring(p.details.mower.activity),
    mower_state=tostring(p.details.mower.state),
    error_code=toint(p.details.mower.error_code),
    battery_percent=toint(p.details.mower.battery_percent),
    decision_code=tostring(p.decision_code),
    command_sent=tobool(p.command_sent),
    hydrawise_available=tobool(p.details.hydrawise.safety.available),
    hydrawise_fresh=tobool(p.details.hydrawise.safety.fresh),
    hydrawise_active_zones=toint(p.details.hydrawise.safety.active_zone_count),
    next_irrigation_start_utc=tostring(p.details.irrigation_outage_guard.next_scheduled_start_utc),
    blocked_source=tostring(p.details.current_plan.blocked_now.source),
    parking_source=tostring(p.details.current_plan.parking_block.source),
    work_area_type=tostring(p.details.mower.target_work_area.type),
    work_area_progress=toint(p.details.mower.target_work_area.progress),
    work_area_last_completed=tolong(p.details.mower.target_work_area.last_time_completed)
| order by timestamp asc
""".strip()


def _exception_query(period_start_utc: datetime, period_end_utc: datetime) -> str:
    start = period_start_utc.isoformat().replace("+00:00", "Z")
    end = period_end_utc.isoformat().replace("+00:00", "Z")
    return (
        "exceptions "
        f"| where timestamp between (datetime({start}) .. datetime({end})) "
        "| where operation_Name == 'ssv53_mower_timer' "
        "| summarize exception_count=sum(itemCount)"
    )


def parse_cycle_rows(rows: Sequence[Mapping[str, Any]]) -> list[CycleObservation]:
    observations: dict[datetime, CycleObservation] = {}
    for row in rows:
        timestamp = _parse_utc(row.get("timestamp"))
        if timestamp is None:
            continue
        minute = timestamp.replace(second=0, microsecond=0)
        observations[minute] = CycleObservation(
            timestamp_utc=timestamp,
            activity=str(row.get("activity") or "UNKNOWN").upper(),
            mower_state=str(row.get("mower_state") or "UNKNOWN").upper(),
            error_code=_as_int(row.get("error_code")),
            battery_percent=_as_int(row.get("battery_percent")),
            decision_code=str(row.get("decision_code") or ""),
            command_sent=_as_bool(row.get("command_sent")),
            hydrawise_available=_as_bool(row.get("hydrawise_available")),
            hydrawise_fresh=_as_bool(row.get("hydrawise_fresh")),
            hydrawise_active_zones=_as_int(row.get("hydrawise_active_zones")),
            next_irrigation_start_utc=_parse_utc(row.get("next_irrigation_start_utc")),
            blocked_source=str(row.get("blocked_source") or ""),
            parking_source=str(row.get("parking_source") or ""),
            work_area_type=str(row.get("work_area_type") or "").upper(),
            work_area_progress=_as_int(row.get("work_area_progress")),
            work_area_last_completed=_as_int(row.get("work_area_last_completed")),
        )
    return sorted(observations.values(), key=lambda item: item.timestamp_utc)


def summarize_report(
    observations: Sequence[CycleObservation],
    *,
    now_utc: datetime,
    exception_count_24h: int,
) -> DailyReportSummary:
    local_now = now_utc.astimezone(REPORT_TIME_ZONE)
    start_day = local_now.date() - timedelta(days=6)
    period_start_local = datetime.combine(start_day, time.min, REPORT_TIME_ZONE)
    period_start_utc = period_start_local.astimezone(timezone.utc)
    relevant = [item for item in observations if period_start_utc <= item.timestamp_utc <= now_utc]
    last_24h_start = now_utc - timedelta(hours=24)
    last_24h = [item for item in relevant if item.timestamp_utc >= last_24h_start]
    daily_minutes: dict[date, int] = defaultdict(int)
    for item in relevant:
        if item.activity in MOWING_ACTIVITIES:
            daily_minutes[item.timestamp_utc.astimezone(REPORT_TIME_ZONE).date()] += 1
    days = tuple((start_day + timedelta(days=index), daily_minutes[start_day + timedelta(days=index)]) for index in range(7))
    mowing_minutes = sum(minutes for _, minutes in days)
    completion_values = {
        item.work_area_last_completed
        for item in relevant
        if item.work_area_last_completed > 0
        and datetime.fromtimestamp(
            item.work_area_last_completed, timezone.utc
        ) >= period_start_utc - timedelta(hours=3)
    }
    completed_cycles = len(completion_values)
    last_completed_area_utc = (
        datetime.fromtimestamp(max(completion_values), timezone.utc)
        if completion_values
        else None
    )
    gaps = 0
    for previous, current in zip(last_24h, last_24h[1:]):
        if current.timestamp_utc - previous.timestamp_utc > timedelta(seconds=90):
            gaps += 1
    expected_cycles = 24 * 60
    latest = relevant[-1] if relevant else None
    warnings: list[str] = []
    if not latest:
        warnings.append("Keine aktuelle Mähertelemetrie vorhanden")
    else:
        if latest.error_code or latest.mower_state in {"ERROR", "FATAL_ERROR", "ERROR_AT_POWER_UP"}:
            warnings.append(
                f"Mäherfehler aktiv: Zustand {latest.mower_state}, Fehlercode {latest.error_code}"
            )
        if not latest.hydrawise_available or not latest.hydrawise_fresh:
            warnings.append("Hydrawise ist nicht frisch und vollständig erreichbar")
    if len(last_24h) < expected_cycles * 0.95:
        warnings.append(
            f"Nur {len(last_24h)} von ungefähr {expected_cycles} Minutenzyklen vorhanden"
        )
    if gaps:
        warnings.append(f"{gaps} Telemetrielücke(n) über 90 Sekunden erkannt")
    if exception_count_24h:
        warnings.append(f"{exception_count_24h} Mäher-Timer-Ausnahme(n) in 24 Stunden")
    return DailyReportSummary(
        report_date=local_now.date(),
        period_start_local=period_start_local,
        period_end_local=local_now,
        cycle_count_24h=len(last_24h),
        expected_cycles_24h=expected_cycles,
        gap_count_24h=gaps,
        exception_count_24h=max(0, int(exception_count_24h)),
        command_count_24h=sum(1 for item in last_24h if item.command_sent),
        mowing_minutes_7d=mowing_minutes,
        average_daily_mowing_minutes_7d=round(mowing_minutes / 7),
        completed_area_cycles_7d=completed_cycles,
        current_work_area_progress=latest.work_area_progress if latest else 0,
        last_completed_area_utc=last_completed_area_utc,
        daily_mowing_minutes=days,
        current_activity=latest.activity if latest else "UNBEKANNT",
        current_state=latest.mower_state if latest else "UNBEKANNT",
        current_error_code=latest.error_code if latest else 0,
        current_battery_percent=latest.battery_percent if latest else 0,
        hydrawise_available=latest.hydrawise_available if latest else False,
        hydrawise_fresh=latest.hydrawise_fresh if latest else False,
        hydrawise_active_zones=latest.hydrawise_active_zones if latest else 0,
        next_irrigation_start_utc=latest.next_irrigation_start_utc if latest else None,
        blocked_source=latest.blocked_source if latest else "",
        parking_source=latest.parking_source if latest else "",
        overall_status="Abweichung" if warnings else "planmäßig",
        warnings=tuple(warnings),
    )


def dashboard_statistics(
    now_utc: datetime,
    values: Mapping[str, str],
    *,
    query_client: ApplicationInsightsQueryClient | Any | None = None,
) -> dict[str, Any]:
    """Returns the established seven-day mowing metrics without sending mail."""

    local_now = now_utc.astimezone(REPORT_TIME_ZONE)
    period_start_local = datetime.combine(
        local_now.date() - timedelta(days=6), time.min, REPORT_TIME_ZONE
    )
    client = query_client or ApplicationInsightsQueryClient.from_environment(values)
    rows = client.execute(
        _cycle_query(period_start_local.astimezone(timezone.utc), now_utc),
        timespan="P8D",
    )
    summary = summarize_report(
        parse_cycle_rows(rows),
        now_utc=now_utc,
        exception_count_24h=0,
    )
    return {
        "available": True,
        "mowingMinutes7d": summary.mowing_minutes_7d,
        "averageDailyMowingMinutes7d": summary.average_daily_mowing_minutes_7d,
        "completedAreaCycles7d": summary.completed_area_cycles_7d,
        "lastCompletedAreaUtc": (
            summary.last_completed_area_utc.isoformat()
            if summary.last_completed_area_utc is not None
            else None
        ),
    }


def _minutes(value: int) -> str:
    hours, minutes = divmod(max(0, int(value)), 60)
    return f"{hours} Std. {minutes:02d} Min."


def _local_time(value: datetime | None) -> str:
    if value is None:
        return "Nicht geplant"
    return value.astimezone(REPORT_TIME_ZONE).strftime("%d.%m.%Y, %H:%M Uhr")


def build_message(
    settings: OrderMailSettings,
    recipient: str,
    summary: DailyReportSummary,
) -> EmailMessage:
    warning_text = " · ".join(summary.warnings) if summary.warnings else "Keine Abweichung erkannt"
    daily_rows = "".join(
        f"<tr><td style='padding:7px 0;border-bottom:1px solid {APP_BORDER};color:{APP_MUTED};'>"
        f"{day.strftime('%a, %d.%m.')}</td><td align='right' style='padding:7px 0;"
        f"border-bottom:1px solid {APP_BORDER};font-weight:700;'>{html.escape(_minutes(minutes))}</td></tr>"
        for day, minutes in summary.daily_mowing_minutes
    )
    facts = [
        ("Aktueller Zustand", f"{summary.current_state} / {summary.current_activity}"),
        ("Akku", f"{summary.current_battery_percent} %"),
        ("Fehlercode", str(summary.current_error_code or "kein Fehler")),
        ("Minutenzyklen 24 h", f"{summary.cycle_count_24h} / ca. {summary.expected_cycles_24h}"),
        ("Lücken über 90 Sek.", str(summary.gap_count_24h)),
        ("Ausnahmen 24 h", str(summary.exception_count_24h)),
        ("Befehle 24 h", str(summary.command_count_24h)),
        ("Mähzeit 7 Tage", _minutes(summary.mowing_minutes_7d)),
        ("Ø Mähzeit pro Tag", _minutes(summary.average_daily_mowing_minutes_7d)),
        ("Aktueller Flächenfortschritt", f"{summary.current_work_area_progress} %"),
        ("Bestätigte Abschlüsse (7 Tage)", f"{summary.completed_area_cycles_7d}"),
        ("Letzter bestätigter Abschluss", _local_time(summary.last_completed_area_utc)),
        (
            "Hydrawise",
            "frisch und erreichbar"
            if summary.hydrawise_available and summary.hydrawise_fresh
            else "nicht sicher verfügbar",
        ),
        ("Aktive Zonen", str(summary.hydrawise_active_zones)),
        ("Nächste Beregnung", _local_time(summary.next_irrigation_start_utc)),
        ("Aktuelle Sperre", summary.blocked_source or "keine"),
        ("Parkgrund", summary.parking_source or "keiner"),
    ]
    fact_rows = "".join(
        f"<tr><td style='width:45%;padding:10px 10px 10px 0;border-bottom:1px solid {APP_BORDER};"
        f"color:{APP_MUTED};font-size:13px;'>{html.escape(label)}</td>"
        f"<td style='padding:10px 0;border-bottom:1px solid {APP_BORDER};font-weight:700;'>"
        f"{html.escape(value)}</td></tr>"
        for label, value in facts
    )
    status_color = "#B42318" if summary.warnings else "#157347"
    subject = f"SSV53 Sicherheitsbericht: {summary.overall_status} – {summary.report_date.strftime('%d.%m.%Y')}"
    plain = "\n".join(
        [
            "SSV53 Sicherheitsbericht",
            f"Gesamturteil: {summary.overall_status}",
            f"Hinweise: {warning_text}",
            "",
            *[f"{label}: {value}" for label, value in facts],
            "",
            "Mähzeiten der letzten sieben Tage:",
            *[f"{day.strftime('%d.%m.%Y')}: {_minutes(minutes)}" for day, minutes in summary.daily_mowing_minutes],
            "",
            "Bestätigte Flächenabschlüsse werden ausschließlich aus dem Husqvarna-EPOS-Feld "
            "lastTimeCompleted ermittelt. Mähzeit und Heimfahrten gelten nicht als Abschluss.",
        ]
    )
    html_body = f"""<!doctype html>
<html lang="de"><body style="margin:0;background:#fff;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;color:#171717;">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0"><tr><td align="center" style="padding:28px 16px;">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width:580px;">
<tr><td align="center"><img src="{APP_LOGO_URL}" alt="SSV53" width="76" style="display:block;width:76px;height:auto;"></td></tr>
<tr><td align="center" style="padding-top:15px;font-size:30px;font-weight:800;">Sicherheitsbericht</td></tr>
<tr><td style="padding:14px 55px 22px;"><div style="height:2px;background:{APP_GOLD};"></div></td></tr>
<tr><td style="padding:0 4px 18px;"><div style="border-left:4px solid {status_color};background:#F7F9FC;padding:14px 16px;">
<div style="font-size:12px;font-weight:800;color:{status_color};text-transform:uppercase;letter-spacing:.5px;">{html.escape(summary.overall_status)}</div>
<div style="padding-top:5px;line-height:1.5;">{html.escape(warning_text)}</div></div></td></tr>
<tr><td><table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="border-collapse:collapse;">{fact_rows}</table></td></tr>
<tr><td style="padding-top:25px;font-size:13px;font-weight:800;color:#285EA7;">MÄHZEITEN – LETZTE 7 TAGE</td></tr>
<tr><td><table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="border-collapse:collapse;">{daily_rows}</table></td></tr>
<tr><td style="padding-top:22px;color:{APP_MUTED};font-size:12px;line-height:1.55;">Bestätigte Flächenabschlüsse werden ausschließlich aus dem Husqvarna-EPOS-Feld lastTimeCompleted ermittelt. Mähzeit, Heimfahrten und automatische Neustarts gelten nicht als Abschluss.</td></tr>
<tr><td style="padding-top:24px;color:{APP_MUTED};font-size:12px;">Automatischer, ausschließlich lesender Bericht aus Azure · Schönwalder SV 1953 e.V.</td></tr>
</table></td></tr></table></body></html>"""
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = formataddr((settings.from_name, settings.from_address))
    message["To"] = recipient
    message.set_content(plain)
    message.add_alternative(html_body, subtype="html")
    return message


MailSender = Callable[[OrderMailSettings, EmailMessage], None]


def _send_message(settings: OrderMailSettings, message: EmailMessage) -> None:
    with _open_authenticated_smtp(settings) as smtp:
        smtp.send_message(message)


def process_daily_report(
    now_utc: datetime,
    values: Mapping[str, str],
    *,
    query_client: ApplicationInsightsQueryClient | Any | None = None,
    store: AzureDailyReportStore | Any | None = None,
    mail_sender: MailSender = _send_message,
) -> dict[str, Any]:
    if not enabled(values):
        return {"enabled": False, "sent": False, "reason": "disabled"}
    if not should_attempt_delivery(now_utc):
        return {"enabled": True, "sent": False, "reason": "outside_delivery_window"}
    recipient = report_recipient(values)
    settings = OrderMailSettings.from_mapping(values)
    local_now = now_utc.astimezone(REPORT_TIME_ZONE)
    directory = store or AzureDailyReportStore.from_environment(values)
    if not directory.claim(local_now.date(), now_utc):
        return {"enabled": True, "sent": False, "reason": "already_claimed"}
    try:
        client = query_client or ApplicationInsightsQueryClient.from_environment(values)
        start_local = datetime.combine(
            local_now.date() - timedelta(days=6), time.min, REPORT_TIME_ZONE
        )
        cycle_rows = client.execute(
            _cycle_query(start_local.astimezone(timezone.utc), now_utc),
            timespan="P8D",
        )
        exception_rows = client.execute(
            _exception_query(now_utc - timedelta(hours=24), now_utc),
            timespan="P2D",
        )
        exception_count = _as_int(
            exception_rows[0].get("exception_count") if exception_rows else 0
        )
        summary = summarize_report(
            parse_cycle_rows(cycle_rows),
            now_utc=now_utc,
            exception_count_24h=exception_count,
        )
        mail_sender(settings, build_message(settings, recipient, summary))
        directory.mark(local_now.date(), "sent", now_utc)
        return {
            "enabled": True,
            "sent": True,
            "status": summary.overall_status,
            "cycles_24h": summary.cycle_count_24h,
            "mowing_minutes_7d": summary.mowing_minutes_7d,
            "completed_area_cycles_7d": summary.completed_area_cycles_7d,
        }
    except Exception:
        directory.mark(local_now.date(), "failed", now_utc)
        raise
