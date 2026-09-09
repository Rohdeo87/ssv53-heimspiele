"""No real endpoint or storage calls; exercise the real signature boundary."""
import json
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import azure.functions as func
import function_app
from platzwart_console import issue_session, PlatzwartError

NOW = datetime(2026, 9, 9, 8, tzinfo=timezone.utc)
ENV = {"SSV53_PLATZWART_SESSION_SECRET": "synthetic-test-key-" * 3,
       "SSV53_SPECIAL_OCCUPANCY_ENABLED": "true"}


def request(path, token=None, body=None, method="POST"):
    headers = {"Content-Type": "application/json"}
    if token is not None:
        headers["Authorization"] = "Bearer " + token
    return func.HttpRequest(method=method, url="https://example.test/" + path,
                            params={}, headers=headers, body=json.dumps(body or {}).encode())


class OccupancyAuthorizationTests(unittest.TestCase):
    def test_no_credentials_or_forged_admin_never_reaches_storage(self):
        forged = {"isAppAdministrator": True, "requesterId": "trusted-user",
                  "creator": {"id": "trusted-user"}, "confirmation": "TRAINING_FAELLT_AUS"}
        with patch.dict(function_app.os.environ, ENV), patch.object(
            function_app.AzureTableCancellationStore, "from_environment"
        ) as cancellations, patch.object(
            function_app.AzureTableSpecialOccupancyStore, "from_environment"
        ) as special:
            for token in (None, "forged.signature"):
                for endpoint in (function_app.ssv53_training_cancellations,
                                 function_app.ssv53_trainer_occupancies):
                    response = endpoint(request("test", token, forged))
                    self.assertEqual(response.status_code, 401)
            cancellations.assert_not_called()
            special.assert_not_called()

    def test_verified_session_defines_subject_and_role(self):
        token, _ = issue_session(ENV, NOW, "enrolled-test-device")
        body = {"requesterId": "someone-else", "isAppAdministrator": False,
                "creator": {"id": "someone-else", "name": "Fixture"}}
        with patch.dict(function_app.os.environ, ENV):
            result = function_app._authorize_occupancy_write(request("test", token), body, NOW)
        self.assertEqual(result["requesterId"], "platzwart:enrolled-test-device")
        self.assertEqual(result["creator"]["id"], result["requesterId"])
        self.assertTrue(result["isAppAdministrator"])
        self.assertEqual(body["creator"]["id"], "someone-else")

    def test_expired_or_other_issuer_key_denied(self):
        expired, _ = issue_session(ENV, NOW - timedelta(hours=1), "device")
        foreign, _ = issue_session({**ENV, "SSV53_PLATZWART_SESSION_SECRET": "different-key-" * 4}, NOW, "device")
        with patch.dict(function_app.os.environ, ENV):
            for token in (expired, foreign):
                with self.assertRaises(PlatzwartError):
                    function_app._authorize_occupancy_write(request("test", token), {}, NOW)

    def test_preflight_can_never_change_occupancy(self):
        for endpoint in (function_app.ssv53_training_cancellations, function_app.ssv53_trainer_occupancies):
            response = endpoint(request("test", method="OPTIONS"))
            self.assertEqual(response.status_code, 204)
            self.assertIn("Authorization", response.headers["Access-Control-Allow-Headers"])
