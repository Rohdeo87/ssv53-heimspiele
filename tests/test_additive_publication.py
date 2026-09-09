"""No network or device calls: complete source -> durable publication -> runtime."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import shutil
import subprocess
import sys
from zoneinfo import ZoneInfo

import pytest

from mower.planner import read_match_blocks
from notify_changes import build_alert, process
from occupancy.service import _structured_match_events
from poc_scraper import Match, evaluate_quality, recalculate_event_times, write_outputs
from publish_matches import PublicationError, decode_included_matches, prepare_bundle
from report_changes import markdown_report
from scripts.build_runtime_config_bundle import RuntimeBundleError, _structured_matches_payload, build_runtime_bundle

ROOT = Path(__file__).resolve().parents[1]
UTC = timezone.utc
NOW = datetime(2026, 9, 9, 6, tzinfo=UTC)
TZ = ZoneInfo("Europe/Berlin")


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


@pytest.fixture
def config():
    return read(ROOT / "config.json")


def game(config, key="A", *, kickoff="2026-09-12T18:00+02:00", calendar="Rasen"):
    result = Match(
        external_id=key, match_number=key, team_id="TEAM", team_name="Schönwalder SV C",
        team_category="C-Junioren", team_role="home", kickoff=kickoff,
        home_team="Schönwalder SV C", away_team="Gast", competition="Liga | C-Junioren",
        match_type="ME", status="", venue_raw="Sportplatz Schönwalde Strandbad, Platz " +
        ("1" if calendar == "Rasen" else "2"), detail_url="https://www.fussball.de/spiel/" + key,
        source_url=config["club_url"], decision="include", calendar=calendar,
        venue_rule=calendar, checksum=key,
    )
    recalculate_event_times(result, config)
    return result


class Factory:
    def __init__(self, root, config):
        self.root, self.config, self.sequence = root, config, 0

    def path(self, kind):
        self.sequence += 1
        return self.root / f"{self.sequence}-{kind}"

    def candidate(self, matches, at=NOW, *, config=None):
        config = config or self.config
        target = self.path("source")
        audits = [{"date_from": config["date_from"], "date_to": config["date_to"],
                   "competition_rows": len(matches), "accepted": True,
                   "truncated": False, "has_more": False,
                   "missing_detail_ids": [], "duplicate_detail_ids": []}]
        quality = evaluate_quality(matches, audits, config, 1)
        assert quality["publishable"], quality
        write_outputs(target, matches, quality, {"teams": [], "changes": {}})
        write(target / "failed_teams.json", [])
        summary = read(target / "summary.json")
        summary["generated_at"] = at.isoformat()
        write(target / "summary.json", summary)
        write(target / "matches.json", _structured_matches_payload(
            [match for match in matches if match.decision == "include"], generated_at=at))
        return target

    def publish(self, before, candidate, *, now=None, **kwargs):
        output = self.path("published")
        report = prepare_bundle(before_dir=before, candidate_dir=candidate, output_dir=output,
                                config=self.config, now=now or NOW, **kwargs)
        return output, report


@pytest.fixture
def factory(tmp_path, config):
    return Factory(tmp_path, config)


def holds(directory):
    return [row for row in read(directory / "matches.json")["matches"] if row.get("publicationRetention")]


def assert_coherent(directory):
    feed = read(directory / "matches.json")
    rows = feed["matches"]
    raw = decode_included_matches(read(directory / "included_matches.json"))
    assert {row["id"] for row in rows} == {"dfb:" + row["external_id"] for row in raw}
    assert len(rows) == len({row["id"] for row in rows}) == len(raw)
    summary = read(directory / "summary.json")
    quality = read(directory / "quality_report.json")
    assert len(rows) == summary["included"]
    for calendar in ("Rasen", "Kunstrasen"):
        selected = [row for row in rows if row["calendar"] == calendar]
        blocks = read_match_blocks(directory / f"{calendar.lower()}.ics", TZ)
        assert len(selected) == len(blocks) == summary["by_calendar"][calendar] == quality["by_calendar"][calendar]
        by_uid = {block.details["uid"]: block for block in blocks}
        for row in selected:
            block = by_uid["dfb-" + row["id"].removeprefix("dfb:") + "@ssv53.de"]
            assert block.start.astimezone(UTC) == datetime.fromisoformat(row["occupancyStart"]).astimezone(UTC)
            assert block.end.astimezone(UTC) == datetime.fromisoformat(row["occupancyEnd"]).astimezone(UTC)
    assert summary["total"] == len(read(directory / "all_matches.json"))


@pytest.mark.parametrize("change", ["time", "place", "new_id"])
def test_new_and_relocated_blocks_publish_immediately_but_old_block_stays(factory, config, change):
    old = game(config)
    keep = game(config, "KEEP")
    before = factory.candidate([old, keep], NOW - timedelta(hours=4))
    replacement = game(config, "TARGET" if change == "new_id" else "A",
                       kickoff="2026-09-12T18:15+02:00" if change != "place" else old.kickoff,
                       calendar="Kunstrasen" if change == "place" else "Rasen")
    candidate = factory.candidate([replacement, keep, game(config, "NEW")])
    output, report = factory.publish(before, candidate)
    assert report["status"] == "additive_pending"
    assert report["counts"]["effectiveAfter"] == 4
    held = holds(output)
    assert len(held) == 1
    assert held[0]["publicationRetention"]["sourceId"] == "dfb:A"
    assert held[0]["occupancyStart"] == old.event_start
    assert held[0]["occupancyEnd"] == old.event_end
    assert held[0]["title"].startswith("Sperre bis Klärung:")
    assert {"dfb:NEW", "dfb:" + replacement.external_id} <= {row["id"] for row in read(output / "matches.json")["matches"]}
    assert_coherent(output)


def test_confirmations_survive_copy_restart_and_same_file_replay(factory, config):
    keep = game(config, "KEEP")
    before = factory.candidate([game(config), keep], NOW - timedelta(hours=4))
    candidate = factory.candidate([keep, game(config, "NEW")])
    first, report = factory.publish(before, candidate)
    held_id = holds(first)[0]["id"]
    restarted = factory.path("restart-copy")
    shutil.copytree(first, restarted)
    replay, report = factory.publish(restarted, candidate, now=NOW + timedelta(hours=2))
    assert holds(replay)[0]["id"] == held_id
    assert holds(replay)[0]["publicationRetention"]["confirmations"] == 1
    assert read(replay / "matches.json")["generatedAt"] == NOW.isoformat()
    soon = factory.candidate([keep, game(config, "NEW")], NOW + timedelta(minutes=59))
    second, _ = factory.publish(replay, soon, now=NOW + timedelta(minutes=59))
    assert holds(second)[0]["publicationRetention"]["confirmations"] == 1
    # An unrelated addition must not reset the pending removal's own confirmation.
    later = factory.candidate([keep, game(config, "NEW"), game(config, "OTHER")], NOW + timedelta(hours=1))
    last, report = factory.publish(second, later, now=NOW + timedelta(hours=1))
    assert not holds(last)
    assert report["released"][0]["reason"] == "confirmed"
    assert report["released"][0]["confirmations"] == 2
    assert read(last / "publication_state.json")["pending"] == {}
    assert_coherent(last)


def test_multiple_moves_retain_every_unconfirmed_version_without_duplicate_ids(factory, config):
    keep = game(config, "KEEP")
    before = factory.candidate([game(config), keep], NOW - timedelta(hours=4))
    next_games = [game(config, kickoff="2026-09-12T19:00+02:00"), keep]
    first, _ = factory.publish(before, factory.candidate(next_games))
    first_id = holds(first)[0]["id"]
    third_games = [game(config, kickoff="2026-09-12T20:00+02:00"), keep]
    later = NOW + timedelta(hours=1)
    second, _ = factory.publish(first, factory.candidate(third_games, later), now=later)
    assert len(holds(second)) == 2
    assert first_id in {row["id"] for row in holds(second)}
    assert {row["publicationRetention"]["confirmations"] for row in holds(second)} == {1}
    last, _ = factory.publish(second, factory.candidate(third_games, later + timedelta(hours=1)),
                              now=later + timedelta(hours=1))
    assert not holds(last)
    assert_coherent(second)
    assert_coherent(last)


def test_returned_source_covers_old_hold_but_does_not_silently_free_newly_observed_slot(factory, config):
    original = game(config)
    before = factory.candidate([original], NOW - timedelta(hours=4))
    moved = factory.candidate([game(config, kickoff="2026-09-12T19:00+02:00")])
    first, _ = factory.publish(before, moved)
    returned, _ = factory.publish(first, factory.candidate([original], NOW + timedelta(hours=1)),
                                 now=NOW + timedelta(hours=1))
    assert len(holds(returned)) == 1
    assert holds(returned)[0]["start"] == "2026-09-12T19:00+02:00"
    assert "dfb:A" in {row["id"] for row in read(returned / "matches.json")["matches"]}
    assert_coherent(returned)


@pytest.mark.parametrize("damage", ["missing", "json", "inflated", "future", "wrong_fingerprint"])
def test_damaged_confirmation_state_restarts_safely(factory, config, damage):
    keep = game(config, "KEEP")
    before = factory.candidate([game(config), keep], NOW - timedelta(hours=4))
    first, _ = factory.publish(before, factory.candidate([keep]))
    state_path = first / "publication_state.json"
    state = read(state_path)
    entry = next(iter(state["pending"].values()))
    if damage == "missing":
        state_path.unlink()
    elif damage == "json":
        state_path.write_text("{", encoding="utf-8")
    else:
        if damage == "inflated":
            entry["confirmations"] = 999
        elif damage == "future":
            entry["lastCountedAt"] = (NOW + timedelta(days=10)).isoformat()
        else:
            entry["fingerprint"] = "wrong"
        write(state_path, state)
    later = NOW + timedelta(hours=2)
    output, _ = factory.publish(first, factory.candidate([keep], later), now=later)
    assert holds(output)[0]["publicationRetention"]["confirmations"] == 1


def test_mass_removal_is_latched_even_when_next_source_has_no_new_removals(factory, config):
    before = factory.candidate([game(config, f"OLD{i}") for i in range(12)], NOW - timedelta(hours=4))
    source = [game(config, "NEW")]
    first, _ = factory.publish(before, factory.candidate(source))
    assert len(holds(first)) == 12
    (first / "publication_state.json").unlink()  # Manual gate also survives loss of observation counters.
    for hour in (1, 2, 3):
        at = NOW + timedelta(hours=hour)
        first, report = factory.publish(first, factory.candidate(source, at), now=at)
        assert report["counts"]["removed"] == 0
        assert len(holds(first)) == 12
        assert all(row["publicationRetention"]["requiresManual"] for row in holds(first))
    assert_coherent(first)


def test_empty_import_does_not_refresh_or_advance_confirmation(factory, config):
    before = factory.candidate([game(config)], NOW - timedelta(hours=4))
    bytes_before = (before / "matches.json").read_bytes()
    candidate = factory.candidate([])
    for hour in (0, 1, 2):
        with pytest.raises(PublicationError, match="leerer Abruf"):
            factory.publish(before, candidate, now=NOW + timedelta(hours=hour))
    assert (before / "matches.json").read_bytes() == bytes_before
    assert not (before / "publication_state.json").exists()


def test_explicit_override_still_requires_valid_complete_source(factory, config):
    before = factory.candidate([game(config)], NOW - timedelta(hours=4))
    candidate = factory.candidate([])
    output, report = factory.publish(before, candidate, allow_destructive=True)
    assert read(output / "matches.json")["matches"] == []
    assert report["guard"]["overrideUsed"] is True
    assert report["released"][0]["reason"] == "manual_override"
    quality = read(candidate / "quality_report.json")
    quality["window_audits"][0]["truncated"] = True
    write(candidate / "quality_report.json", quality)
    with pytest.raises(PublicationError):
        factory.publish(before, candidate, allow_destructive=True)


@pytest.mark.parametrize("damage", [
    "failed", "quality", "review", "partial_raw", "duplicate", "invalid_feed", "wrong_time",
    "wrong_calendar", "wrong_count", "wrong_partition", "missing_audits", "gap", "has_more",
    "truncated", "missing_ids", "bad_accepted_type", "reserved_id",
])
def test_broken_sources_publish_nothing_even_with_valid_additions(factory, config, damage):
    before = factory.candidate([game(config)], NOW - timedelta(hours=4))
    candidate = factory.candidate([game(config), game(config, "NEW")])
    before_bytes = {path.name: path.read_bytes() for path in before.iterdir() if path.is_file()}
    filename = "quality_report.json"
    if damage == "failed":
        filename, value = "failed_teams.json", [{"team": "Missing"}]
    elif damage == "review":
        filename, value = "review_matches.json", [{}]
    elif damage in {"partial_raw", "duplicate", "reserved_id"}:
        filename = "included_matches.json"
        value = read(candidate / filename)
        if damage == "partial_raw":
            value.pop()
        elif damage == "duplicate":
            value.append(deepcopy(value[0]))
        else:
            value[0]["external_id"] = "held-fake"
    elif damage in {"invalid_feed", "wrong_time", "wrong_calendar"}:
        filename = "matches.json"
        value = read(candidate / filename)
        if damage == "invalid_feed":
            value["matches"] = {}
        else:
            value["matches"][0]["start" if damage == "wrong_time" else "calendar"] = "wrong"
    elif damage == "wrong_count":
        filename, value = "summary.json", read(candidate / "summary.json")
        value["included"] = 1
    elif damage == "wrong_partition":
        filename, value = "all_matches.json", []
    else:
        value = read(candidate / filename)
        if damage == "quality":
            value["publishable"] = False
        elif damage == "missing_audits":
            value["window_audits"] = []
        elif damage == "gap":
            value["window_audits"][0]["date_from"] = "2026-09-01"
        elif damage == "missing_ids":
            value["window_audits"][0]["missing_detail_ids"] = ["LOST"]
        elif damage == "bad_accepted_type":
            value["window_audits"][0]["accepted"] = "true"
        else:
            value["window_audits"][0][damage] = True
    write(candidate / filename, value)
    output = factory.path("must-not-exist")
    with pytest.raises(ValueError):
        prepare_bundle(before_dir=before, candidate_dir=candidate, output_dir=output, config=config, now=NOW)
    assert not output.exists()
    assert {path.name: path.read_bytes() for path in before.iterdir() if path.is_file()} == before_bytes


def test_outside_source_horizon_needs_explicit_review_and_does_not_auto_confirm(factory, config):
    before = factory.candidate([game(config)], NOW - timedelta(hours=4))
    next_season = deepcopy(config)
    next_season.update(date_from="2027-07-01", date_to="2028-06-30")
    factory.config = next_season
    future = [game(config, "NEW", kickoff="2027-09-12T18:00+02:00")]
    for hour in (0, 1, 2):
        at = NOW + timedelta(hours=hour)
        before, _ = factory.publish(before, factory.candidate(future, at), now=at)
        assert holds(before)[0]["publicationRetention"]["requiresManual"] is True


@pytest.mark.parametrize("clock_error", ["older", "future", "same_time_different_content"])
def test_invalid_source_chronology_is_rejected(factory, config, clock_error):
    first, _ = factory.publish(factory.path("empty"), factory.candidate([game(config)]))
    at = {"older": NOW - timedelta(minutes=1), "future": NOW + timedelta(minutes=6),
          "same_time_different_content": NOW}[clock_error]
    candidate = factory.candidate([game(config), game(config, "NEW")], at)
    with pytest.raises(PublicationError):
        factory.publish(first, candidate)


def test_runtime_rebuild_preserves_held_duration_and_marks_both_app_and_ics(factory, config):
    old = game(config)
    old.match_end = "2026-09-12T19:45+02:00"
    old.event_end = "2026-09-12T20:45+02:00"
    old.match_duration_minutes = 105
    old.duration_rule = "previous-approved-rule"
    before = factory.candidate([old], NOW - timedelta(hours=4))
    output, _ = factory.publish(before, factory.candidate([game(config)]))
    held = holds(output)[0]
    assert held["matchDurationMinutes"] == 105
    bundle = factory.path("runtime")
    build_runtime_bundle(
        mower_config_path=ROOT / "mower/config.json", timing_config_path=ROOT / "config.json",
        included_matches_path=output / "included_matches.json", source_summary_path=output / "summary.json",
        source_quality_path=output / "quality_report.json", output_dir=bundle, version="test",
        published_at=NOW, source_commit="a" * 40, max_source_age_minutes=720)
    public = bundle / "versions/test/public"
    runtime = read(public / "matches.json")
    runtime_hold = next(row for row in runtime["matches"] if row.get("publicationRetention"))
    assert runtime_hold == held
    blocks = read_match_blocks(public / "rasen.ics", TZ)
    held_block = next(block for block in blocks if "held-" in block.details["uid"])
    assert held_block.end.astimezone(UTC) == datetime.fromisoformat(held["occupancyEnd"]).astimezone(UTC)
    app_events = _structured_match_events(
        read(ROOT / "mower/config.json"), matches_path=public / "matches.json",
        range_start=datetime(2026, 9, 12, tzinfo=TZ), range_end=datetime(2026, 9, 13, tzinfo=TZ), tz=TZ)
    assert len(app_events) == 2
    assert any(event["title"].startswith("Sperre bis Klärung:") for event in app_events)


@pytest.mark.parametrize("kickoff", ["2026-09-13T00:30+02:00", "2026-10-25T02:15+02:00", "2026-10-25T02:15+01:00"])
def test_ics_and_json_preserve_midnight_and_both_fold_instants(factory, config, kickoff):
    model = game(config, kickoff=kickoff)
    # Export the same real instants in UTC, as an alternative valid source encoding.
    for name in ("kickoff", "match_end", "event_start", "event_end"):
        setattr(model, name, datetime.fromisoformat(getattr(model, name)).astimezone(UTC).isoformat())
    candidate = factory.candidate([model])
    output, _ = factory.publish(factory.path("empty"), candidate)
    assert_coherent(output)


def test_report_and_notification_distinguish_retained_from_removed_source_rows(factory, config):
    before = factory.candidate([game(config), game(config, "KEEP")], NOW - timedelta(hours=4))
    output, report = factory.publish(before, factory.candidate([game(config, "KEEP"), game(config, "NEW")]))
    text = markdown_report(report)
    assert "ungeklärte Rücknahmen bleiben gesperrt" in text
    assert "Wirksame Spiele und Sperren: 3" in text
    alert = build_alert(report=report, scrape_outcome="success", feed_outcome="success",
                        changes_outcome="success", persist_outcome="success", run_url="")
    assert alert.alert_type == "retained"
    assert "weiterhin nicht frei" in alert.body
    later = NOW + timedelta(minutes=5)
    _, replay_report = factory.publish(output, factory.candidate([game(config, "KEEP"), game(config, "NEW")], later), now=later)
    replay_alert = build_alert(report=replay_report, scrape_outcome="success", feed_outcome="success",
                               changes_outcome="success", persist_outcome="success", run_url="")
    assert replay_alert.fingerprint == alert.fingerprint


def test_cli_runs_real_create_feed_then_prepares_additive_bundle(factory, config):
    before = factory.candidate([game(config)], NOW - timedelta(hours=4))
    source = factory.candidate([game(config, "NEW")])
    candidate = factory.path("created-feed")
    output = factory.path("publication")
    subprocess.run([sys.executable, str(ROOT / "create_feed.py"), "--input", str(source),
                    "--output", str(candidate)], cwd=ROOT, check=True, capture_output=True, text=True)
    # Freeze only the test process clock; the production CLI has no fake-freshness flag.
    harness = (
        "import sys; from datetime import datetime, timezone; import publish_matches\n"
        "class Clock(datetime):\n"
        " @classmethod\n"
        " def now(cls, tz=None): return datetime(2026,9,9,6,tzinfo=timezone.utc).astimezone(tz)\n"
        "publish_matches.datetime=Clock; sys.argv=sys.argv[1:]; raise SystemExit(publish_matches.main())\n"
    )
    subprocess.run([sys.executable, "-c", harness, "publish_matches.py", "--before", str(before),
                    "--candidate", str(candidate), "--output", str(output),
                    "--json", str(factory.root / "report.json"), "--markdown", str(factory.root / "report.md")],
                   cwd=ROOT, check=True, capture_output=True, text=True)
    assert len(holds(output)) == 1
    assert_coherent(output)


def test_cancellation_as_excluded_source_record_is_retained_then_confirmed(factory, config):
    keep = game(config, "KEEP")
    before = factory.candidate([game(config), keep], NOW - timedelta(hours=4))
    cancelled = game(config)
    cancelled.status = "Absetzung"
    cancelled.decision = "exclude"
    candidate = factory.candidate([cancelled, keep, game(config, "NEW")])
    first, _ = factory.publish(before, candidate)
    assert len(holds(first)) == 1
    assert len(read(first / "source_all_matches.json")) == 3
    assert len(read(first / "all_matches.json")) == 4
    assert read(first / "excluded_matches.json")[0]["external_id"] == "A"
    later = NOW + timedelta(hours=1)
    final, _ = factory.publish(first, factory.candidate([cancelled, keep, game(config, "NEW")], later), now=later)
    assert not holds(final)
    assert_coherent(first)
    assert_coherent(final)


def test_interrupted_ics_generation_does_not_replace_bundle_or_advance_state(factory, config, monkeypatch):
    import publish_matches
    keep = game(config, "KEEP")
    before = factory.candidate([game(config), keep], NOW - timedelta(hours=4))
    first, _ = factory.publish(before, factory.candidate([keep]))
    snapshot = {path.name: path.read_bytes() for path in first.iterdir() if path.is_file()}
    candidate = factory.candidate([keep], NOW + timedelta(hours=1))
    real_write = publish_matches.write_ics
    calls = []

    def fail_second_ics(path, matches, name):
        calls.append(path)
        if len(calls) == 2:
            raise OSError("Injected interrupted write")
        real_write(path, matches, name)

    monkeypatch.setattr(publish_matches, "write_ics", fail_second_ics)
    output = factory.path("interrupted")
    with pytest.raises(OSError, match="Injected"):
        prepare_bundle(before_dir=first, candidate_dir=candidate, output_dir=output, config=config,
                       now=NOW + timedelta(hours=1))
    assert not output.exists()
    assert not list(output.parent.glob(output.name + ".*"))
    assert {path.name: path.read_bytes() for path in first.iterdir() if path.is_file()} == snapshot


@pytest.mark.parametrize("target", ["before", "candidate", "existing", "inside_before"])
def test_publisher_never_overwrites_an_input_or_existing_output(factory, config, target):
    before = factory.candidate([game(config)], NOW - timedelta(hours=4))
    candidate = factory.candidate([game(config), game(config, "NEW")])
    existing = factory.path("existing")
    existing.mkdir()
    marker = existing / "user-file.txt"
    marker.write_text("preserve", encoding="utf-8")
    output = {"before": before, "candidate": candidate, "existing": existing,
              "inside_before": before / "child"}[target]
    with pytest.raises(PublicationError, match="getrennte Verzeichnisse"):
        prepare_bundle(before_dir=before, candidate_dir=candidate, output_dir=output, config=config, now=NOW)
    assert marker.read_text(encoding="utf-8") == "preserve"


def test_expired_retention_ends_without_waiting_for_irrelevant_future_confirmation(factory, config):
    # Source timestamps remain the source timestamps even though the next run is later.
    keep = game(config, "KEEP", kickoff="2026-09-15T18:00+02:00")
    before = factory.candidate([game(config), keep], NOW - timedelta(hours=4))
    first, _ = factory.publish(before, factory.candidate([keep]))
    later = NOW + timedelta(days=4)
    output, report = factory.publish(first, factory.candidate([keep], later), now=later)
    assert not holds(output)
    assert report["released"][0]["reason"] == "expired"


@pytest.mark.parametrize("still_pending", [True, False])
def test_pending_warning_stays_open_until_removals_are_resolved(factory, config, still_pending):
    before = factory.candidate([game(config), game(config, "KEEP")], NOW - timedelta(hours=4))
    _, report = factory.publish(before, factory.candidate([game(config, "KEEP")]))
    alert = build_alert(report=report, scrape_outcome="success", feed_outcome="success",
                        changes_outcome="success", persist_outcome="success", run_url="")

    class Client:
        closed = []

        def open_issues(self):
            return [{"number": 1, "body": alert.body, "html_url": "https://example.test/issue/1"}]

        def comment(self, number, text):
            pass

        def close(self, number):
            self.closed.append(number)

    client = Client()
    action, _ = process(client=client, alert=alert if still_pending else None,
                        technical_success=True, dry_run=False)
    assert client.closed == ([] if still_pending else [1])
    assert action == ("duplicate" if still_pending else "none")


def test_corrupt_source_sidecar_cannot_bypass_mass_removal_gate(factory, config):
    initial = factory.candidate([game(config, f"OLD{i}") for i in range(12)])
    before, _ = factory.publish(factory.path("empty"), initial)
    source = read(before / "source_matches.json")
    source["matches"] = []
    write(before / "source_matches.json", source)
    later = NOW + timedelta(hours=1)
    with pytest.raises(PublicationError, match="Veröffentlichungsnachweis"):
        factory.publish(before, factory.candidate([game(config, "NEW")], later), now=later)


@pytest.mark.parametrize("field", ["sourceCount", "effectiveCount", "retainedCount", "sourceGeneratedAt", "schemaVersion"])
def test_corrupt_publication_evidence_is_not_used_as_comparison_basis(factory, config, field):
    before, _ = factory.publish(factory.path("empty"), factory.candidate([game(config)]))
    feed = read(before / "matches.json")
    feed["publication"][field] = "2020-01-01T00:00Z" if field == "sourceGeneratedAt" else 999
    write(before / "matches.json", feed)
    at = NOW + timedelta(hours=1)
    with pytest.raises(PublicationError, match="Veröffentlichungsnachweis"):
        factory.publish(before, factory.candidate([game(config), game(config, "NEW")], at), now=at)


def test_missing_primary_files_do_not_turn_an_existing_calendar_into_empty_baseline(factory, config):
    before = factory.candidate([game(config)], NOW - timedelta(hours=4))
    (before / "matches.json").unlink()
    (before / "included_matches.json").unlink()
    with pytest.raises(PublicationError, match="unvollständig"):
        factory.publish(before, factory.candidate([game(config, "NEW")]))


@pytest.mark.parametrize("concurrent_publication", [False, True])
def test_real_git_tree_guard_refuses_lost_update_from_second_publisher(tmp_path, concurrent_publication):
    # Run the exact comparison block from the workflow in an isolated local Git
    # repository; no fetch, push or external process is started by this test.
    import os
    import textwrap
    bash = shutil.which("bash")
    if not bash:
        bundled_git_bash = Path("C:/Program Files/Git/bin/bash.exe")
        bash = str(bundled_git_bash) if bundled_git_bash.is_file() else None
    if not bash:
        pytest.skip("Bash is needed to execute the workflow's actual guard")
    repo = tmp_path / "git"
    repo.mkdir()

    def git(*args):
        return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()

    git("init", "-q")
    git("config", "user.name", "Test")
    git("config", "user.email", "test@example.invalid")
    (repo / "public").mkdir()
    (repo / "public/matches.json").write_text("original", encoding="utf-8")
    git("add", "public")
    git("commit", "-qm", "baseline")
    original_tree = git("rev-parse", "HEAD:public")
    if concurrent_publication:
        (repo / "public/matches.json").write_text("new concurrent publication", encoding="utf-8")
        git("add", "public")
        git("commit", "-qm", "other publisher")
    git("update-ref", "refs/remotes/origin/test", "HEAD")
    state = repo / "state"
    state.mkdir()
    (state / "previous-public-tree").write_text(original_tree, encoding="utf-8")
    workflow = (ROOT / ".github/workflows/update-matches.yml").read_text(encoding="utf-8")
    fragment = workflow[workflow.index('          CURRENT_PUBLIC_TREE='):workflow.index('          git reset --hard "origin/$TARGET_BRANCH"')]
    environment = {**os.environ, "TEMP_DIR": "state", "TARGET_BRANCH": "test", "SAVE_PUBLIC": "true"}
    result = subprocess.run([bash, "-c", "set -euo pipefail\n" + textwrap.dedent(fragment)],
                            cwd=repo, env=environment, capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == (1 if concurrent_publication else 0), result.stderr
    assert (repo / "public/matches.json").read_text(encoding="utf-8") == (
        "new concurrent publication" if concurrent_publication else "original")


def test_verified_old_consumer_accepts_legacy_lists_but_rejects_retention_container(factory, config):
    import runpy
    legacy = runpy.run_path(str(ROOT / "tests/fixtures/runtime_bundle_legacy_list_decoder_9c2d0fc.py"))["_load_list"]
    before = factory.candidate([game(config)], NOW - timedelta(hours=4))
    assert len(legacy(before / "included_matches.json", "source")) == 1
    output, _ = factory.publish(before, factory.candidate([game(config, "NEW")]))
    with pytest.raises(ValueError, match="nicht leere JSON-Liste"):
        legacy(output / "included_matches.json", "source")


@pytest.mark.parametrize("damage", [
    "flattened", "version", "consumer", "same_count_wrong_row", "count", "summary_time",
    "mixed_quality", "quality_calendar", "missing_retention", "primary_feed_mismatch",
])
def test_runtime_rejects_incompatible_or_mixed_publication_before_writing_manifest(factory, config, damage):
    before = factory.candidate([game(config)], NOW - timedelta(hours=4))
    output, _ = factory.publish(before, factory.candidate([game(config, "NEW")]))
    included = read(output / "included_matches.json")
    if damage in {"summary_time", "mixed_quality", "quality_calendar"}:
        filename = "summary.json" if damage == "summary_time" else "quality_report.json"
        payload = read(output / filename)
        if damage == "summary_time":
            payload["generated_at"] = (NOW - timedelta(minutes=1)).isoformat()
        elif damage == "quality_calendar":
            payload["by_calendar"]["Rasen"] += 1
        else:
            other, _ = factory.publish(before, factory.candidate([game(config, "OTHER")]))
            payload = read(other / "quality_report.json")  # Same counts, different actual publication.
        write(output / filename, payload)
    elif damage == "primary_feed_mismatch":
        # The producer verifies its primary JSON too, before the consumer takes over.
        payload = read(output / "matches.json")
        payload["publication"]["effectiveDigest"] = "0" * 64
        write(output / "matches.json", payload)
        later = NOW + timedelta(hours=1)
        with pytest.raises(PublicationError):
            factory.publish(output, factory.candidate([game(config, "NEW")], later), now=later)
        return
    else:
        if damage == "flattened":
            included = included["matches"]
        elif damage == "version":
            included["schemaVersion"] = 3
        elif damage == "consumer":
            included["requiredConsumer"] = "unknown-future-version"
        elif damage == "same_count_wrong_row":
            included["matches"][0]["event_end"] = "2026-09-12T23:45+02:00"
        elif damage == "count":
            included["publication"]["retainedCount"] = 0
        else:
            next(row for row in included["matches"] if row.get("publication_retention")).pop("publication_retention")
        write(output / "included_matches.json", included)
    bundle = factory.path("rejected-runtime")
    with pytest.raises(RuntimeBundleError):
        build_runtime_bundle(
            mower_config_path=ROOT / "mower/config.json", timing_config_path=ROOT / "config.json",
            included_matches_path=output / "included_matches.json", source_summary_path=output / "summary.json",
            source_quality_path=output / "quality_report.json", output_dir=bundle, version="reject",
            published_at=NOW, source_commit="a" * 40, max_source_age_minutes=720)
    assert not (bundle / "current/manifest.json").exists()
