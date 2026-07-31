#!/usr/bin/env python3
"""Erzeugt bei relevanten SSV53-Spielabrufen deduplizierte GitHub-Issues."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

API_VERSION = "2022-11-28"
MANAGED_MARKER = "<!-- ssv53-managed-alert -->"
FINGERPRINT_PREFIX = "<!-- ssv53-alert:"
TYPE_PREFIX = "<!-- ssv53-alert-type:"
RECOVERABLE_TYPES = {"failure", "blocked"}


@dataclass(frozen=True)
class Alert:
    alert_type: str
    title: str
    body: str
    fingerprint: str


def load_report(path: Path | None) -> dict[str, Any] | None:
    if path is None or not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Änderungsbericht ist nicht lesbar: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("Änderungsbericht muss ein JSON-Objekt sein.")
    return payload


def display_datetime(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return "–"
    try:
        from datetime import datetime

        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed.strftime("%d.%m.%Y %H:%M")
    except ValueError:
        return text


def match_label(match: dict[str, Any]) -> str:
    team = str(match.get("team") or "Unbekannte Mannschaft").strip()
    home = str(match.get("homeTeam") or "?").strip()
    away = str(match.get("awayTeam") or "?").strip()
    kickoff = display_datetime(match.get("kickoff"))
    calendar = str(match.get("calendar") or "ohne Platz").strip()
    return f"{team} · {home} – {away} · {kickoff} · {calendar}"


def fingerprint_for(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]


def change_fingerprint_payload(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": report.get("status"),
        "counts": report.get("counts", {}),
        "added": [str(item.get("id", "")) for item in report.get("added", [])],
        "removed": [str(item.get("id", "")) for item in report.get("removed", [])],
        "changed": [
            {
                "id": str(item.get("id", "")),
                "changes": [
                    {
                        "field": change.get("field"),
                        "before": change.get("before"),
                        "after": change.get("after"),
                    }
                    for change in item.get("changes", [])
                ],
            }
            for item in report.get("changed", [])
        ],
    }


def append_change_sections(lines: list[str], report: dict[str, Any], max_items: int = 30) -> None:
    def add_simple(title: str, items: list[dict[str, Any]]) -> None:
        if not items:
            return
        lines.extend([f"## {title}", ""])
        for item in items[:max_items]:
            lines.append(f"- {match_label(item)}")
        if len(items) > max_items:
            lines.append(f"- … und {len(items) - max_items} weitere")
        lines.append("")

    add_simple("Neue Spiele", list(report.get("added", [])))

    changed = list(report.get("changed", []))
    if changed:
        lines.extend(["## Geänderte Spiele", ""])
        for item in changed[:max_items]:
            lines.append(f"- **{item.get('label') or match_label(item.get('after', {}))}**")
            for change in item.get("changes", []):
                before = change.get("before", "")
                after = change.get("after", "")
                if change.get("field") in {"kickoff", "start", "end"}:
                    before = display_datetime(before)
                    after = display_datetime(after)
                lines.append(
                    f"  - {change.get('label') or change.get('field')}: "
                    f"`{before or '–'}` → `{after or '–'}`"
                )
        if len(changed) > max_items:
            lines.append(f"- … und {len(changed) - max_items} weitere")
        lines.append("")

    add_simple("Entfernte Spiele", list(report.get("removed", [])))


def build_alert(
    *,
    report: dict[str, Any] | None,
    scrape_outcome: str,
    feed_outcome: str,
    changes_outcome: str,
    persist_outcome: str,
    run_url: str,
    force_test: bool = False,
    test_id: str = "",
) -> Alert | None:
    if force_test:
        fp = fingerprint_for({"type": "test", "test_id": test_id or run_url})
        body = "\n".join(
            [
                "## Test erfolgreich",
                "",
                "Diese Benachrichtigung bestätigt, dass der SSV53-Spielabruf GitHub-Issues erzeugen und dem Repository-Inhaber zuweisen kann.",
                "",
                f"[Workflow-Lauf öffnen]({run_url})" if run_url else "",
                "",
                MANAGED_MARKER,
                f"{FINGERPRINT_PREFIX}{fp} -->",
                f"{TYPE_PREFIX}test -->",
            ]
        ).strip() + "\n"
        return Alert("test", "🔔 SSV53-Testbenachrichtigung", body, fp)

    scrape_ok = scrape_outcome == "success"
    feed_ok = feed_outcome == "success"
    changes_ok = changes_outcome == "success"
    persist_ok = persist_outcome == "success"

    if not scrape_ok:
        stage = "Abruf von FUSSBALL.DE"
        fp = fingerprint_for({"type": "failure", "stage": "scrape"})
        alert_type = "failure"
        title = "🔴 SSV53-Spielabruf fehlgeschlagen"
        explanation = "Die Spieldaten konnten nicht vollständig abgerufen werden. Der letzte veröffentlichte Stand bleibt unverändert."
    elif not feed_ok:
        stage = "Erstellung des veröffentlichten Feeds"
        fp = fingerprint_for({"type": "failure", "stage": "feed"})
        alert_type = "failure"
        title = "🔴 SSV53-Spielabruf fehlgeschlagen"
        explanation = "Der Feed konnte nicht sicher erstellt werden. Der letzte veröffentlichte Stand bleibt unverändert."
    elif not changes_ok:
        blocked = bool(report and report.get("status") == "blocked")
        alert_type = "blocked" if blocked else "failure"
        stage = "Sicherheitsprüfung" if blocked else "Auswertung der Änderungen"
        fp = fingerprint_for(
            {
                "type": alert_type,
                "stage": "changes",
                "report": change_fingerprint_payload(report or {}),
            }
        )
        title = "⛔ SSV53-Veröffentlichung blockiert" if blocked else "🔴 SSV53-Änderungsprüfung fehlgeschlagen"
        explanation = (
            "Die Sicherheitsprüfung hat eine unplausible Datenänderung erkannt. "
            "Der letzte veröffentlichte Stand bleibt unverändert."
            if blocked
            else "Die Änderungen konnten nicht sicher ausgewertet werden. Der letzte veröffentlichte Stand bleibt unverändert."
        )
    elif not persist_ok:
        alert_type = "failure"
        stage = "Speichern und Veröffentlichen"
        fp = fingerprint_for({"type": "failure", "stage": "persist"})
        title = "🔴 SSV53-Veröffentlichung fehlgeschlagen"
        explanation = (
            "Die geprüften Daten konnten nicht sicher in GitHub gespeichert werden. "
            "Der zuletzt veröffentlichte Stand ist weiterhin maßgeblich."
        )
    else:
        counts = (report or {}).get("counts", {})
        added = int(counts.get("added", 0) or 0)
        changed = int(counts.get("changed", 0) or 0)
        removed = int(counts.get("removed", 0) or 0)
        if added + changed + removed == 0:
            return None

        alert_type = "change"
        stage = "Änderung erkannt"
        fp = fingerprint_for({"type": "change", "report": change_fingerprint_payload(report or {})})
        if removed:
            title = f"🔴 SSV53-Spielabruf: {added + changed + removed} Änderung(en)"
        elif changed:
            title = f"🟡 SSV53-Spielabruf: {added + changed} Änderung(en)"
        else:
            title = f"🔵 SSV53-Spielabruf: {added} neue(s) Spiel(e)"
        explanation = "Der neue Stand wurde erfolgreich geprüft und veröffentlicht."

    lines = [
        "## Handlungsbedarf",
        "",
        f"**Bereich:** {stage}",
        "",
        explanation,
        "",
    ]

    if report:
        counts = report.get("counts", {})
        lines.extend(
            [
                "| Kennzahl | Anzahl |",
                "|---|---:|",
                f"| Spiele vorher | {counts.get('before', '–')} |",
                f"| Spiele nachher | {counts.get('after', '–')} |",
                f"| Neu | {counts.get('added', 0)} |",
                f"| Geändert | {counts.get('changed', 0)} |",
                f"| Entfernt | {counts.get('removed', 0)} |",
                "",
            ]
        )
        reasons = ((report.get("guard") or {}).get("reasons") or [])
        if reasons:
            lines.extend(["## Sicherheitsprüfung", ""])
            lines.extend(f"- {reason}" for reason in reasons)
            lines.append("")
        append_change_sections(lines, report)

    if run_url:
        lines.extend([f"[Workflow-Lauf öffnen]({run_url})", ""])

    lines.extend(
        [
            MANAGED_MARKER,
            f"{FINGERPRINT_PREFIX}{fp} -->",
            f"{TYPE_PREFIX}{alert_type} -->",
        ]
    )
    return Alert(alert_type, title, "\n".join(lines).strip() + "\n", fp)


def marker_value(body: str, prefix: str) -> str | None:
    start = body.find(prefix)
    if start < 0:
        return None
    value_start = start + len(prefix)
    end = body.find(" -->", value_start)
    if end < 0:
        return None
    return body[value_start:end].strip()


class GitHubClient:
    def __init__(self, repository: str, token: str):
        if "/" not in repository:
            raise ValueError("Repository muss im Format owner/name angegeben werden.")
        self.repository = repository
        self.token = token
        self.owner = repository.split("/", 1)[0]

    def request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        url = f"https://api.github.com/repos/{self.repository}{path}"
        data = None
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=data,
            method=method,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": API_VERSION,
                "User-Agent": "ssv53-spielabruf",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                raw = response.read()
                return json.loads(raw.decode("utf-8")) if raw else None
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"GitHub API {method} {path} fehlgeschlagen: HTTP {exc.code}: {detail}") from exc

    def open_issues(self) -> list[dict[str, Any]]:
        payload = self.request("GET", "/issues?state=open&per_page=100")
        return [item for item in (payload or []) if "pull_request" not in item]

    def create_issue(self, alert: Alert) -> dict[str, Any]:
        payload = {
            "title": alert.title,
            "body": alert.body,
            "assignees": [self.owner],
        }
        try:
            return self.request("POST", "/issues", payload)
        except RuntimeError as exc:
            if "HTTP 422" not in str(exc):
                raise
            payload.pop("assignees", None)
            return self.request("POST", "/issues", payload)

    def comment(self, number: int, body: str) -> None:
        self.request("POST", f"/issues/{number}/comments", {"body": body})

    def close(self, number: int) -> None:
        self.request("PATCH", f"/issues/{number}", {"state": "closed", "state_reason": "completed"})


def append_summary(lines: list[str]) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY", "").strip()
    if not path:
        return
    with Path(path).open("a", encoding="utf-8") as handle:
        handle.write("\n## GitHub-Benachrichtigung\n\n")
        handle.write("\n".join(lines).rstrip() + "\n")


def set_output(name: str, value: str) -> None:
    path = os.environ.get("GITHUB_OUTPUT", "").strip()
    if not path:
        return
    with Path(path).open("a", encoding="utf-8") as handle:
        handle.write(f"{name}={value}\n")


def process(
    *,
    client: GitHubClient,
    alert: Alert | None,
    technical_success: bool,
    dry_run: bool,
) -> tuple[str, str]:
    open_issues = client.open_issues() if not dry_run else []

    if technical_success:
        for issue in open_issues:
            body = str(issue.get("body") or "")
            issue_type = marker_value(body, TYPE_PREFIX)
            if MANAGED_MARKER in body and issue_type in RECOVERABLE_TYPES:
                if not dry_run:
                    client.comment(
                        int(issue["number"]),
                        "✅ Der SSV53-Spielabruf ist wieder erfolgreich. Diese automatische Warnung wird geschlossen.",
                    )
                    client.close(int(issue["number"]))

    if alert is None:
        return "none", ""

    for issue in open_issues:
        body = str(issue.get("body") or "")
        if marker_value(body, FINGERPRINT_PREFIX) == alert.fingerprint:
            return "duplicate", str(issue.get("html_url") or "")

    if dry_run:
        return "dry-run", ""

    issue = client.create_issue(alert)
    return "created", str(issue.get("html_url") or "")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", required=True)
    parser.add_argument("--token", default=os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or "")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--scrape-outcome", default="success")
    parser.add_argument("--feed-outcome", default="success")
    parser.add_argument("--changes-outcome", default="success")
    parser.add_argument("--persist-outcome", default="success")
    parser.add_argument("--run-url", default="")
    parser.add_argument("--test", action="store_true")
    parser.add_argument("--test-id", default="")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not args.token and not args.dry_run:
        parser.error("GitHub-Token fehlt.")

    try:
        report = load_report(args.report)
        alert = build_alert(
            report=report,
            scrape_outcome=args.scrape_outcome,
            feed_outcome=args.feed_outcome,
            changes_outcome=args.changes_outcome,
            persist_outcome=args.persist_outcome,
            run_url=args.run_url,
            force_test=args.test,
            test_id=args.test_id,
        )
        technical_success = (
            not args.test
            and args.scrape_outcome == "success"
            and args.feed_outcome == "success"
            and args.changes_outcome == "success"
            and args.persist_outcome == "success"
        )
        client = GitHubClient(args.repository, args.token or "dry-run")
        action, issue_url = process(
            client=client,
            alert=alert,
            technical_success=technical_success,
            dry_run=args.dry_run,
        )
    except (ValueError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        append_summary([f"❌ Benachrichtigung fehlgeschlagen: {exc}"])
        return 1

    if action == "created":
        message = f"✅ GitHub-Issue erstellt: {issue_url}"
    elif action == "duplicate":
        message = f"ℹ️ Identische offene Benachrichtigung existiert bereits: {issue_url}"
    elif action == "dry-run":
        message = "ℹ️ Trockenlauf: Benachrichtigung würde erstellt."
    else:
        message = "✅ Kein Benachrichtigungsbedarf."

    print(message)
    append_summary([message])
    set_output("action", action)
    set_output("issue_url", issue_url)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
