from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest

from scripts.build_runtime_config_bundle import (
    RuntimeBundleError,
    build_runtime_bundle,
)


ROOT = Path(__file__).resolve().parents[1]


def _match(
    external_id: str,
    *,
    kickoff: str,
    team: str,
    category: str,
    calendar: str = "Rasen",
    match_type: str = "ME",
) -> dict[str, object]:
    return {
        "external_id": external_id,
        "match_number": external_id[-3:],
        "team_id": f"team-{external_id}",
        "team_name": team,
        "team_category": category,
        "team_role": "home",
        "kickoff": kickoff,
        "home_team": team,
        "away_team": "Gastverein",
        "competition": f"Liga | {category}",
        "match_type": match_type,
        "status": "",
        "venue_raw": f"Sportplatz Schönwalde Strandbad, Platz {'1' if calendar == 'Rasen' else '2'}",
        "detail_url": f"https://example.test/{external_id}",
        "source_url": "https://example.test/matchplan",
        "decision": "include",
        "calendar": calendar,
        "venue_rule": calendar,
        "event_start": "2000-01-01T00:00+01:00",
        "event_end": "2000-01-01T01:00+01:00",
        "checksum": external_id,
        "warnings": [],
    }


class RuntimeConfigBundleTests(unittest.TestCase):
    published_at = datetime(2026, 8, 11, 16, 30, tzinfo=timezone.utc)

    def _source(self, directory: Path) -> tuple[Path, Path, Path]:
        included = [
            _match(
                "match-c",
                kickoff="2026-08-19T18:00+02:00",
                team="Schönwalder SV C",
                category="C-Junioren",
            ),
            _match(
                "match-d",
                kickoff="2026-08-20T19:00+02:00",
                team="Schönwalder SV D",
                category="D-Junioren",
            ),
            _match(
                "match-herren",
                kickoff="2026-08-21T20:00+02:00",
                team="Schönwalder SV Herren",
                category="Herren",
            ),
            _match(
                "match-e-kr",
                kickoff="2026-08-22T10:00+02:00",
                team="Schönwalder SV E",
                category="E-Junioren",
                calendar="Kunstrasen",
            ),
        ]
        summary = {
            "generated_at": "2026-08-11T12:30:00+00:00",
            "publishable": True,
            "review": 0,
            "included": len(included),
            "by_calendar": {"Rasen": 3, "Kunstrasen": 1},
        }
        quality = {"publishable": True, "errors": []}
        included_path = directory / "included_matches.json"
        summary_path = directory / "summary.json"
        quality_path = directory / "quality_report.json"
        included_path.write_text(json.dumps(included), encoding="utf-8")
        summary_path.write_text(json.dumps(summary), encoding="utf-8")
        quality_path.write_text(json.dumps(quality), encoding="utf-8")
        return included_path, summary_path, quality_path

    def _build(self, directory: Path, **overrides: object) -> dict[str, object]:
        included, summary, quality = self._source(directory)
        arguments: dict[str, object] = {
            "mower_config_path": ROOT / "mower" / "config.json",
            "timing_config_path": ROOT / "config.json",
            "included_matches_path": included,
            "source_summary_path": summary,
            "source_quality_path": quality,
            "output_dir": directory / "bundle",
            "version": "20260811T163000Z-test",
            "published_at": self.published_at,
            "source_commit": "a" * 40,
            "max_source_age_minutes": 720,
        }
        arguments.update(overrides)
        return build_runtime_bundle(**arguments)  # type: ignore[arg-type]

    def test_retimes_youth_matches_and_keeps_every_rasen_match(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            summary = self._build(directory)
            self.assertEqual(summary["matches_loaded"], 3)
            self.assertEqual(summary["by_age_class"]["C"], 1)
            self.assertEqual(summary["by_age_class"]["D"], 1)
            self.assertEqual(summary["by_age_class"]["HERREN"], 1)
            self.assertEqual(summary["by_age_class"]["E"], 1)
            self.assertEqual(summary["safety"]["training_before_minutes"], 30)
            self.assertEqual(summary["safety"]["training_after_minutes"], 30)
            self.assertEqual(summary["safety"]["hydrawise_before_minutes"], 30)
            self.assertEqual(summary["safety"]["hydrawise_after_minutes"], 0)

            ics = (
                directory
                / "bundle"
                / "versions"
                / "20260811T163000Z-test"
                / "public"
                / "rasen.ics"
            ).read_text(encoding="utf-8")
            self.assertEqual(ics.count("BEGIN:VEVENT"), 3)
            self.assertIn("DTSTART;TZID=Europe/Berlin:20260819T170000", ics)
            self.assertIn("DTEND;TZID=Europe/Berlin:20260819T202500", ics)
            self.assertIn("DTSTART;TZID=Europe/Berlin:20260820T180000", ics)
            self.assertIn("DTEND;TZID=Europe/Berlin:20260820T211500", ics)
            self.assertIn("DTSTART;TZID=Europe/Berlin:20260821T190000", ics)
            self.assertIn("DTEND;TZID=Europe/Berlin:20260821T224500", ics)
            self.assertNotIn("match-e-kr", ics)

    def test_uses_active_range_for_publication_day_without_fixed_date(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            summary = self._build(Path(tmp))
            ranges = summary["safety"]["training_ranges"]
            self.assertIn(
                {"from": "2026-08-11", "to": "2027-07-09"},
                ranges,
            )

    def test_manifest_hashes_and_source_provenance_match_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            summary = self._build(directory)
            manifest = json.loads(
                (directory / "bundle" / "current" / "manifest.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(manifest["source_commit"], "a" * 40)
            self.assertEqual(
                manifest["source_generated_at_utc"],
                "2026-08-11T12:30:00+00:00",
            )
            self.assertEqual(manifest["config_sha256"], summary["config_sha256"])
            self.assertEqual(manifest["matches_sha256"], summary["matches_sha256"])

    def test_rejects_source_before_runtime_manifest_can_hide_staleness(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            included, summary_path, quality = self._source(directory)
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            summary["generated_at"] = "2026-08-11T04:29:00+00:00"
            summary_path.write_text(json.dumps(summary), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeBundleError, "älter als"):
                build_runtime_bundle(
                    mower_config_path=ROOT / "mower" / "config.json",
                    timing_config_path=ROOT / "config.json",
                    included_matches_path=included,
                    source_summary_path=summary_path,
                    source_quality_path=quality,
                    output_dir=directory / "bundle",
                    version="stale-source",
                    published_at=self.published_at,
                    source_commit="b" * 40,
                    max_source_age_minutes=720,
                )

    def test_rejects_unpublishable_or_incomplete_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            included, summary_path, quality = self._source(directory)
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            summary["by_calendar"]["Rasen"] = 2
            summary_path.write_text(json.dumps(summary), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeBundleError, "widersprüchlich"):
                build_runtime_bundle(
                    mower_config_path=ROOT / "mower" / "config.json",
                    timing_config_path=ROOT / "config.json",
                    included_matches_path=included,
                    source_summary_path=summary_path,
                    source_quality_path=quality,
                    output_dir=directory / "bundle",
                    version="missing-match",
                    published_at=self.published_at,
                    source_commit="c" * 40,
                    max_source_age_minutes=720,
                )


if __name__ == "__main__":
    unittest.main()
