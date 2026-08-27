from __future__ import annotations

from datetime import datetime, timezone
import json
from unittest.mock import patch
import unittest

import azure.functions as func

import function_app
from occupancy.runtime_source import OccupancyMatchSource
from special_occupancy import InMemorySpecialOccupancyStore
from training_cancellations import InMemoryCancellationStore


FIXED_NOW = datetime(2026, 8, 27, 10, 0, tzinfo=timezone.utc)


def request() -> func.HttpRequest:
    return func.HttpRequest(
        method="GET",
        url="https://example.test/api/occupancy",
        headers={},
        params={
            "start": "2026-08-27",
            "end": "2026-09-03",
            "season": "Sommer",
        },
        body=b"",
    )


class OccupancyApiResilienceTests(unittest.TestCase):
    def test_read_only_calendar_keeps_last_validated_current_feed_visible(self) -> None:
        source = OccupancyMatchSource(
            matches_path="public/matches.json",
            source_kind="azure_blob",
            source_generated_at_utc="2026-08-26T16:30:00+00:00",
            fresh=False,
            age_minutes=1050,
        )
        cancellation_store = InMemoryCancellationStore()
        special_store = InMemorySpecialOccupancyStore()
        with (
            patch.object(function_app, "datetime", wraps=datetime) as clock,
            patch.object(
                function_app,
                "_occupancy_match_source",
                return_value=source,
            ) as resolver,
            patch.object(
                function_app.AzureTableCancellationStore,
                "from_environment",
                return_value=cancellation_store,
            ),
            patch.object(
                function_app.AzureTableSpecialOccupancyStore,
                "from_environment",
                return_value=special_store,
            ),
            patch.object(function_app, "special_occupancy_enabled", return_value=True),
        ):
            clock.now.return_value = FIXED_NOW
            response = function_app.ssv53_occupancy(request())

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.get_body())
        self.assertFalse(payload["match_source_fresh"])
        self.assertEqual(payload["match_source_age_minutes"], 1050)
        self.assertEqual(
            payload["match_source_warning"],
            "MATCH_SOURCE_UPDATE_DELAYED",
        )
        self.assertTrue(payload["events"])
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        resolver.assert_called_once_with(
            now_utc=FIXED_NOW,
            allow_stale_display=True,
        )

    def test_conflict_checks_do_not_opt_into_display_grace_period(self) -> None:
        source = OccupancyMatchSource(
            matches_path="public/matches.json",
            source_kind="azure_blob",
        )
        store = InMemorySpecialOccupancyStore()
        with patch.object(
            function_app,
            "_occupancy_match_source",
            return_value=source,
        ) as resolver:
            function_app._trainer_occupancy_conflicts(
                start=datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc),
                end=datetime(2026, 8, 27, 13, 0, tzinfo=timezone.utc),
                resource_id="rasen",
                store=store,
            )

        self.assertEqual(resolver.call_count, 1)
        self.assertNotIn("allow_stale_display", resolver.call_args.kwargs)


if __name__ == "__main__":
    unittest.main()
