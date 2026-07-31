import unittest

import notify_changes


def match(match_id: str, *, kickoff: str = "2026-09-01T18:00+02:00"):
    return {
        "id": match_id,
        "team": "SSV53 E2",
        "homeTeam": "SSV53 E2",
        "awayTeam": "Gastverein",
        "kickoff": kickoff,
        "calendar": "Rasen",
    }


def report(*, added=None, changed=None, removed=None, status="ok"):
    added = added or []
    changed = changed or []
    removed = removed or []
    return {
        "status": status,
        "counts": {
            "before": 10,
            "after": 10 + len(added) - len(removed),
            "added": len(added),
            "changed": len(changed),
            "removed": len(removed),
        },
        "guard": {"reasons": []},
        "added": added,
        "changed": changed,
        "removed": removed,
    }


class NotificationTests(unittest.TestCase):
    def test_no_alert_without_changes(self):
        alert = notify_changes.build_alert(
            report=report(),
            scrape_outcome="success",
            feed_outcome="success",
            changes_outcome="success",
            persist_outcome="success",
            run_url="https://example.test/run",
        )
        self.assertIsNone(alert)

    def test_added_match_creates_blue_alert(self):
        alert = notify_changes.build_alert(
            report=report(added=[match("dfb:1")]),
            scrape_outcome="success",
            feed_outcome="success",
            changes_outcome="success",
            persist_outcome="success",
            run_url="https://example.test/run",
        )
        self.assertIsNotNone(alert)
        self.assertEqual(alert.alert_type, "change")
        self.assertIn("🔵", alert.title)
        self.assertIn("SSV53 E2", alert.body)

    def test_removed_match_creates_red_alert(self):
        alert = notify_changes.build_alert(
            report=report(removed=[match("dfb:1")]),
            scrape_outcome="success",
            feed_outcome="success",
            changes_outcome="success",
            persist_outcome="success",
            run_url="https://example.test/run",
        )
        self.assertIsNotNone(alert)
        self.assertIn("🔴", alert.title)

    def test_scrape_failure_creates_failure_alert(self):
        alert = notify_changes.build_alert(
            report=None,
            scrape_outcome="failure",
            feed_outcome="skipped",
            changes_outcome="skipped",
            persist_outcome="skipped",
            run_url="https://example.test/run",
        )
        self.assertIsNotNone(alert)
        self.assertEqual(alert.alert_type, "failure")
        self.assertIn("letzte veröffentlichte Stand", alert.body)

    def test_blocked_report_creates_blocked_alert(self):
        blocked = report(removed=[match("dfb:1")], status="blocked")
        blocked["guard"]["reasons"] = ["Zu viele Spiele würden verschwinden."]
        alert = notify_changes.build_alert(
            report=blocked,
            scrape_outcome="success",
            feed_outcome="success",
            changes_outcome="failure",
            persist_outcome="success",
            run_url="https://example.test/run",
        )
        self.assertIsNotNone(alert)
        self.assertEqual(alert.alert_type, "blocked")
        self.assertIn("Zu viele Spiele", alert.body)

    def test_persist_failure_creates_failure_alert(self):
        alert = notify_changes.build_alert(
            report=report(added=[match("dfb:1")]),
            scrape_outcome="success",
            feed_outcome="success",
            changes_outcome="success",
            persist_outcome="failure",
            run_url="https://example.test/run",
        )
        self.assertIsNotNone(alert)
        self.assertEqual(alert.alert_type, "failure")
        self.assertIn("Veröffentlichung fehlgeschlagen", alert.title)

    def test_fingerprint_ignores_generated_at(self):
        first = report(added=[match("dfb:1")])
        second = report(added=[match("dfb:1")])
        first["generatedAt"] = "2026-07-31T10:00:00Z"
        second["generatedAt"] = "2026-07-31T11:00:00Z"
        first_alert = notify_changes.build_alert(
            report=first,
            scrape_outcome="success",
            feed_outcome="success",
            changes_outcome="success",
            persist_outcome="success",
            run_url="https://example.test/run/1",
        )
        second_alert = notify_changes.build_alert(
            report=second,
            scrape_outcome="success",
            feed_outcome="success",
            changes_outcome="success",
            persist_outcome="success",
            run_url="https://example.test/run/2",
        )
        self.assertEqual(first_alert.fingerprint, second_alert.fingerprint)

    def test_marker_value(self):
        body = "<!-- ssv53-alert:abc123 -->"
        self.assertEqual(
            notify_changes.marker_value(body, notify_changes.FINGERPRINT_PREFIX),
            "abc123",
        )


if __name__ == "__main__":
    unittest.main()
