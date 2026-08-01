from __future__ import annotations

import argparse
import json
import os
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from mower.hydrawise import HydrawiseError, fetch_status
from mower.planner import create_plan, load_json, plan_to_dict, read_match_blocks


WEEKDAY_DE = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]


def _format_time(iso_value: str) -> str:
    return datetime.fromisoformat(iso_value).strftime("%H:%M")


def parse_start_date(value: str) -> date:
    """Liest ISO- und deutsche Datumsformate für die manuelle Workflow-Eingabe."""
    cleaned = value.strip()
    if not cleaned:
        raise ValueError("Das Startdatum darf nicht leer sein.")

    formats = (
        ("%Y-%m-%d", "2026-08-24"),
        ("%d.%m.%Y", "24.08.2026"),
        ("%d.%m.%y", "24.8.26"),
    )
    for date_format, _example in formats:
        try:
            return datetime.strptime(cleaned, date_format).date()
        except ValueError:
            continue

    examples = ", ".join(example for _date_format, example in formats)
    raise ValueError(
        f"Ungültiges Startdatum {value!r}. Erlaubte Beispiele: {examples}."
    )


def render_markdown(plan: dict[str, Any]) -> str:
    metadata = plan["metadata"]
    lines = [
        "# Mähplan – Dry Run",
        "",
        f"Zeitraum: **{metadata['start_date']} bis {metadata['end_date']}**  ",
        f"Hydrawise: **{metadata['hydrawise_status']}**  ",
        f"Mindestfenster: **{metadata['minimum_window_minutes']} Minuten**",
        "",
    ]
    if plan["warnings"]:
        lines.append("## Hinweise")
        lines.extend(f"- {warning}" for warning in plan["warnings"])
        lines.append("")

    lines.extend(["## Berechnete Mähfenster", ""])
    for day in plan["days"]:
        parsed_day = date.fromisoformat(day["date"])
        hours, minutes = divmod(day["available_minutes"], 60)
        lines.append(
            f"### {WEEKDAY_DE[parsed_day.weekday()]}, "
            f"{parsed_day.strftime('%d.%m.%Y')} – {hours} h {minutes:02d} min"
        )
        if day["mowing_windows"]:
            for window in day["mowing_windows"]:
                lines.append(
                    f"- Mähen **{_format_time(window['start'])}–{_format_time(window['end'])}** "
                    f"({window['minutes']} Minuten)"
                )
        else:
            lines.append("- Kein Mähfenster von mindestens der konfigurierten Mindestdauer")
        if day["blocked"]:
            lines.append("- Sperren:")
            for block in day["blocked"]:
                lines.append(
                    f"  - {_format_time(block['start'])}–{_format_time(block['end'])}: "
                    f"{block['title']}"
                )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Berechnet den maximalen SSV53-Mähplan als Dry Run."
    )
    parser.add_argument("--config", default="mower/config.json")
    parser.add_argument("--matches", default="public/rasen.ics")
    parser.add_argument("--output", default="generated/mower")
    parser.add_argument(
        "--start-date",
        default="",
        help=(
            "Startdatum, z. B. 24.8.26, 24.08.2026 oder 2026-08-24; "
            "Standard: heute in Europe/Berlin"
        ),
    )
    parser.add_argument(
        "--days",
        type=int,
        default=0,
        help="Planungstage; 0 nutzt die Konfiguration",
    )
    parser.add_argument(
        "--no-hydrawise",
        action="store_true",
        help="Hydrawise auch bei vorhandenem Secret nicht abrufen",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = load_json(args.config)
    tz = ZoneInfo(config.get("timezone", "Europe/Berlin"))
    planning = config.get("planning", {})
    start_day = (
        parse_start_date(args.start_date)
        if args.start_date
        else datetime.now(tz).date()
    )
    days = args.days or int(planning.get("days", 7))
    if days < 1 or days > 31:
        raise ValueError("Die Zahl der Planungstage muss zwischen 1 und 31 liegen.")

    warnings: list[str] = []
    hydrawise_status: dict[str, Any] | None = None
    hydrawise_label = "nicht konfiguriert"
    hydrawise_config = config.get("hydrawise", {})
    api_key = os.environ.get("HYDRAWISE_API_KEY", "").strip()
    if not args.no_hydrawise and hydrawise_config.get("enabled", True) and api_key:
        controller_env = hydrawise_config.get(
            "controller_id_env",
            "HYDRAWISE_CONTROLLER_ID",
        )
        controller_id = os.environ.get(controller_env, "").strip()
        try:
            hydrawise_status = fetch_status(api_key, controller_id or None)
            relay_count = len(hydrawise_status.get("relays", []))
            hydrawise_label = f"live verbunden ({relay_count} Zonen gelesen)"
        except HydrawiseError as exc:
            hydrawise_label = "Abruf fehlgeschlagen"
            warnings.append(f"Hydrawise konnte nicht gelesen werden: {exc}")
    elif hydrawise_config.get("enabled", True) and not args.no_hydrawise:
        warnings.append(
            "HYDRAWISE_API_KEY fehlt; Beregnungszeiten sind in diesem Dry Run "
            "noch nicht enthalten."
        )

    match_blocks = read_match_blocks(args.matches, tz)
    if not Path(args.matches).exists():
        warnings.append(
            f"Spielkalender {args.matches} fehlt; Heimspiele sind nicht enthalten."
        )

    plans, merged = create_plan(
        config,
        match_blocks,
        hydrawise_status,
        start_day,
        days,
    )
    end_day = date.fromordinal(start_day.toordinal() + days - 1)
    metadata = {
        "generated_at": datetime.now(tz).isoformat(),
        "timezone": str(tz),
        "start_date": start_day.isoformat(),
        "end_date": end_day.isoformat(),
        "days": days,
        "minimum_window_minutes": int(
            planning.get("minimum_mowing_window_minutes", 30)
        ),
        "hydrawise_status": hydrawise_label,
        "matches_loaded": len(match_blocks),
    }
    result = plan_to_dict(plans, merged, warnings, metadata)

    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    json_path = output / "mowing_plan.json"
    markdown_path = output / "mowing_plan.md"
    json_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    markdown = render_markdown(result)
    markdown_path.write_text(markdown, encoding="utf-8")

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with Path(summary_path).open("a", encoding="utf-8") as handle:
            handle.write(markdown)

    print(f"Dry Run erzeugt: {markdown_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
