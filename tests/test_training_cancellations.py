from __future__ import annotations

import json
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from mower.planner import build_training_blocks, resolve_training_cancellation_keys
from occupancy.service import build_occupancy_payload, build_training_occurrences
from training_cancellations import InMemoryCancellationStore


ROOT = Path(__file__).resolve().parents[1]
TZ = ZoneInfo("Europe/Berlin")


class TrainingCancellationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.occurrences = build_training_occurrences(
            config_path=ROOT / "occupancy" / "config.json",
            start="2026-08-11",
            end="2026-08-15",
            season="Sommer",
        )

    def test_only_real_training_occurrences_receive_stable_ids(self) -> None:
        self.assertTrue(self.occurrences)
        self.assertTrue(
            all(item["id"].startswith("training:sommer:") for item in self.occurrences)
        )
        self.assertTrue(all(item["scheduleId"] for item in self.occurrences))
        self.assertTrue(all(item["source"] == "training" for item in self.occurrences))

    def test_cancel_and_restore_are_audited_and_idempotent(self) -> None:
        store = InMemoryCancellationStore()
        now = datetime(2026, 8, 12, 10, tzinfo=timezone.utc)
        item = store.cancel(
            self.occurrences[0],
            now_utc=now,
            release_delay_minutes=30,
        )
        repeated = store.cancel(
            self.occurrences[0],
            now_utc=now + timedelta(minutes=5),
            release_delay_minutes=30,
        )
        self.assertEqual(item, repeated)
        self.assertEqual(item.release_not_before_utc, now + timedelta(minutes=30))
        self.assertFalse(item.is_effective(now + timedelta(minutes=29)))
        self.assertTrue(item.is_effective(now + timedelta(minutes=30)))
        self.assertEqual(len(store.audit), 1)
        self.assertTrue(store.restore(item.event_id, now_utc=now + timedelta(hours=1)))
        self.assertFalse(store.restore(item.event_id, now_utc=now + timedelta(hours=2)))
        self.assertEqual([entry["action"] for entry in store.audit], ["cancel", "restore"])

    def test_calendar_marks_cancelled_training_but_keeps_it_visible(self) -> None:
        target = next(item for item in self.occurrences if item["team"] == "C")
        before = build_occupancy_payload(
            config_path=ROOT / "occupancy" / "config.json",
            matches_path=ROOT / "public" / "matches.json",
            start="2026-08-11",
            end="2026-08-15",
            season="Sommer",
        )
        after = build_occupancy_payload(
            config_path=ROOT / "occupancy" / "config.json",
            matches_path=ROOT / "public" / "matches.json",
            start="2026-08-11",
            end="2026-08-15",
            season="Sommer",
            cancelled_occurrences={(target["scheduleId"], target["start"][:10])},
        )
        self.assertEqual(len(before["events"]), len(after["events"]))
        marked = next(item for item in after["events"] if item["id"] == target["id"])
        self.assertTrue(marked["cancelled"])
        self.assertFalse(
            next(item for item in before["events"] if item["id"] == target["id"])["cancelled"]
        )
        self.assertEqual(
            [item for item in before["events"] if item["source"] != "training"],
            [item for item in after["events"] if item["source"] != "training"],
        )

    def test_same_rasen_ids_drive_calendar_and_mower(self) -> None:
        config = json.loads((ROOT / "mower" / "config.json").read_text(encoding="utf-8"))
        mower_ids = {item["id"] for item in config["training"]["weekly"]}
        occupancy_config = json.loads(
            (ROOT / "occupancy" / "config.json").read_text(encoding="utf-8")
        )
        occupancy_ids = {
            item["id"]
            for item in occupancy_config["seasons"]["Sommer"]["weekly"]
            if item["resource_id"] == "rasen"
        }
        self.assertEqual(mower_ids, occupancy_ids)

    def test_mower_keeps_buffer_until_cancellation_becomes_effective(self) -> None:
        config = json.loads((ROOT / "mower" / "config.json").read_text(encoding="utf-8"))
        training = config["training"]
        day = date(2026, 8, 12)
        normal = build_training_blocks(training, day, 1, TZ)
        self.assertEqual(len(normal), 1)
        self.assertEqual(normal[0].start.strftime("%H:%M"), "17:00")
        cancelled = build_training_blocks(
            training,
            day,
            1,
            TZ,
            {("som-ras-c-mi", day.isoformat())},
        )
        self.assertEqual(cancelled, [])

    def test_cancelled_e1_releases_its_entire_buffer_before_a_training(self) -> None:
        config = json.loads((ROOT / "mower" / "config.json").read_text(encoding="utf-8"))
        day = date(2026, 8, 18)
        blocks = build_training_blocks(
            config["training"],
            day,
            1,
            TZ,
            {("som-ras-e1-di", day.isoformat())},
        )
        self.assertEqual([block.details["team"] for block in blocks], ["A"])
        self.assertEqual(blocks[0].start.strftime("%H:%M"), "18:00")
        self.assertEqual(blocks[0].end.strftime("%H:%M"), "20:30")

    def test_legacy_runtime_without_ids_is_resolved_by_exact_occurrence(self) -> None:
        config = json.loads((ROOT / "mower" / "config.json").read_text(encoding="utf-8"))
        training = config["training"]
        for session in training["weekly"]:
            session.pop("id", None)
        occurrence = next(
            item
            for item in self.occurrences
            if item["scheduleId"] == "som-ras-c-mi"
        )
        store = InMemoryCancellationStore()
        cancellation = store.cancel(
            occurrence,
            now_utc=datetime(2026, 8, 12, 10, tzinfo=timezone.utc),
            release_delay_minutes=0,
        )
        keys, unresolved = resolve_training_cancellation_keys(
            training,
            [cancellation],
            TZ,
        )
        self.assertEqual(unresolved, [])
        self.assertEqual(len(keys), 1)
        self.assertEqual(
            build_training_blocks(training, cancellation.day, 1, TZ, keys),
            [],
        )

    def test_changed_or_ambiguous_legacy_runtime_stays_blocked(self) -> None:
        config = json.loads((ROOT / "mower" / "config.json").read_text(encoding="utf-8"))
        training = config["training"]
        for session in training["weekly"]:
            session.pop("id", None)
        occurrence = next(
            item
            for item in self.occurrences
            if item["scheduleId"] == "som-ras-c-mi"
        )
        store = InMemoryCancellationStore()
        cancellation = store.cancel(
            occurrence,
            now_utc=datetime(2026, 8, 12, 10, tzinfo=timezone.utc),
            release_delay_minutes=0,
        )
        training["weekly"][2]["start"] = "17:31"
        keys, unresolved = resolve_training_cancellation_keys(training, [cancellation], TZ)
        self.assertEqual(keys, set())
        self.assertEqual(unresolved, [cancellation.event_id])
        self.assertTrue(build_training_blocks(training, cancellation.day, 1, TZ, keys))

    def test_existing_appack_plan_has_no_secret_and_requires_confirmation(self) -> None:
        source = (ROOT / "appack-platzbelegungsplan-azure.html").read_text(encoding="utf-8")
        self.assertIn("TRAINING_FAELLT_AUS", source)
        self.assertIn("TRAINING_WIEDER_AKTIV", source)
        self.assertIn("Daten werden geladen", source)
        self.assertNotIn("api-key", source.casefold())
        self.assertNotIn("bearer", source.casefold())


if __name__ == "__main__":
    unittest.main()
