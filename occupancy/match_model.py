from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata
from typing import Any


class MatchTimingError(ValueError):
    """A match cannot be published without a traceable timing rule."""


@dataclass(frozen=True)
class MatchTiming:
    minutes: int
    duration_rule: str
    competition_format: str
    age_class: str


_WEEKDAY = re.compile(
    r"^\s*(?:mo(?:ntag)?|di(?:enstag)?|mi(?:ttwoch)?|do(?:nnerstag)?|"
    r"fr(?:eitag)?|sa(?:mstag)?|so(?:nntag)?)\s*,?\s*(?:[|·]\s*)?",
    re.IGNORECASE,
)
_URL = re.compile(r"https?://[^\s|,;<>]+", re.IGNORECASE)


def _fold(value: str) -> str:
    value = unicodedata.normalize("NFKD", str(value or ""))
    value = "".join(char for char in value if not unicodedata.combining(char))
    value = value.casefold().replace("ß", "ss")
    return re.sub(r"[^a-z0-9]+", " ", value).strip()


def _clean(value: str) -> str:
    return " ".join(unicodedata.normalize("NFC", str(value or "")).split())


def detect_age_class(team_category: str, team_name: str) -> str:
    category = _fold(team_category)
    team = _fold(team_name)
    combined = f"{category} {team}".strip()

    for marker, result in (
        (r"\b(?:ue|u)\s*50\b", "UE50"),
        (r"\b(?:ue|u)\s*40\b", "UE40"),
    ):
        if re.search(marker, combined):
            return result

    for letter in "abcdefg":
        if re.search(rf"(?:^|\s){letter}(?:\s*[-]?\s*(?:junior|jugend)|\d?\b)", category):
            return letter.upper()

    if re.search(r"\bherren\b|\bmaenner\b", combined):
        return "HERREN"
    return ""


def detect_competition_format(
    competition: str,
    match_type: str,
    team_category: str = "",
) -> str:
    text = _fold(f"{competition} {team_category}")
    code = str(match_type or "").strip().upper()
    if re.search(r"\b(?:twin|zwilling|zwillingsmodus)\b", text):
        return "twin"
    if re.search(r"\b(?:kinderfussball|festival|spielfest|spielenachmittag)\b", text):
        return "festival"
    if code == "PO" or re.search(r"\b(?:pokal|entscheidungsspiel|playoff)\b", text):
        return "cup"
    if code in {"FS", "FR"} or re.search(r"\bfreundschaft", text):
        return "friendly"
    if code == "TU" or re.search(r"\bturnier\b", text):
        return "tournament"
    if code == "ME" or re.search(r"\b(?:liga|klasse|meisterschaft)\b", text):
        return "league"
    return "standard"


def resolve_match_timing(
    *,
    team_name: str,
    team_category: str,
    competition: str,
    match_type: str,
    timing_config: dict[str, Any],
    competition_format: str = "",
) -> MatchTiming:
    age_class = detect_age_class(team_category, team_name)
    detected_format = competition_format or detect_competition_format(
        competition,
        match_type,
        team_category,
    )

    for rule in timing_config.get("format_rules", []) or []:
        if str(rule.get("competition_format") or "") != detected_format:
            continue
        configured_age = str(rule.get("age_class") or "")
        if configured_age and configured_age != age_class:
            continue
        minutes = int(rule.get("minutes", 0))
        rule_id = str(rule.get("id") or "").strip()
        if minutes > 0 and rule_id:
            return MatchTiming(minutes, rule_id, detected_format, age_class)

    if detected_format in {"twin", "festival", "tournament"}:
        raise MatchTimingError(
            f"Sonderformat {detected_format!r} erkannt, aber keine belastbare "
            "2026/27-Regel für diese Altersklasse konfiguriert."
        )

    age_rules = timing_config.get("age_class_rules", {}) or {}
    rule = age_rules.get(age_class) if age_class else None
    if isinstance(rule, dict):
        playing_minutes = int(rule.get("minutes", 0))
        halftime_minutes = int(rule.get("halftime_minutes", 0))
        rule_id = str(rule.get("id") or "").strip()
        if playing_minutes <= 0 or halftime_minutes < 0 or not rule_id:
            raise MatchTimingError(f"Unvollständige Zeitregel für Altersklasse {age_class}.")
        minutes = playing_minutes + halftime_minutes
        if halftime_minutes:
            rule_id = f"{rule_id}+halftime-{halftime_minutes}"
        if detected_format == "cup":
            extension = int(rule.get("cup_extension_minutes", 0))
            if extension > 0:
                minutes += extension
                rule_id = f"{rule_id}+cup-max-{extension}"
        return MatchTiming(minutes, rule_id, detected_format, age_class)

    combined = _clean(f"{team_category} {team_name} {competition}")
    for fallback in timing_config.get("fallback_rules", []) or []:
        pattern = str(fallback.get("pattern") or "")
        if not pattern or not re.search(pattern, combined, re.IGNORECASE):
            continue
        minutes = int(fallback.get("minutes", 0))
        rule_id = str(fallback.get("id") or "").strip()
        if minutes > 0 and rule_id:
            return MatchTiming(minutes, rule_id, detected_format, "FALLBACK")

    raise MatchTimingError(
        "Keine belastbare Match-Dauer für "
        f"team={team_name!r}, category={team_category!r}, competition={competition!r}."
    )


def normalize_match_title(home_team: str, away_team: str) -> str:
    home = _clean(home_team)
    away = _clean(away_team)
    if home and away:
        return f"{home} – {away}"
    return home or away or "Heimspiel"


def normalize_match_description(team_category: str, competition: str) -> str:
    values: list[str] = []
    seen: set[str] = set()
    for raw in (team_category, competition):
        cleaned = _WEEKDAY.sub("", _clean(raw))
        for part in re.split(r"[|·]", cleaned):
            part = _WEEKDAY.sub("", _clean(part)).strip(" ,;·")
            key = _fold(part)
            if part and key and key not in seen:
                seen.add(key)
                values.append(part)
    return " · ".join(values)


def normalize_legacy_ics_summary(value: str) -> tuple[str, str]:
    summary = _clean(value)
    team, separator, fixture = summary.partition(":")
    if not separator:
        return summary, ""
    team = team.strip()
    fixture = fixture.strip()
    if not team or not fixture or not _fold(fixture).startswith(_fold(team)):
        return summary, ""
    suffix = fixture[len(team):]
    if suffix and not (suffix[0].isspace() or suffix[0] in "-–—"):
        return summary, ""
    return fixture, team


def normalize_legacy_ics_description(value: str) -> tuple[str, str]:
    description = unicodedata.normalize("NFC", str(value or ""))
    links = _URL.findall(description)
    detail_link = links[0].rstrip(".,;:!?)]}") if links else ""
    description = _URL.sub("", description)
    description = _WEEKDAY.sub("", description, count=1)
    parts = [
        _clean(part).strip(" ,;·")
        for part in re.split(r"[|·]", description)
    ]
    return " · ".join(part for part in parts if part), detail_link
