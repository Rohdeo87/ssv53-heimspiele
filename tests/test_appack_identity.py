"""Provider replies are fixtures; no credentials, network or real appointments."""
import copy
import json
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import requests

import function_app
from occupancy import appack_identity as auth
from platzwart_console import PlatzwartError
from special_occupancy import InMemorySpecialOccupancyStore
from tests.test_occupancy_authorization import request
from training_cancellations import InMemoryCancellationStore


NOW = datetime(2026, 8, 18, 12, tzinfo=timezone.utc)
TOKEN = "synthetic.test.signature"


def identity(**profile_changes):
    return {
        "appScope": "ssv53", "expiresAt": (NOW + timedelta(minutes=10)).isoformat(),
        "userProfile": {
            "id": "appack-42", "appId": "ssv53", "firstname": "Test", "name": "Trainer",
            "email": "test@example.invalid", "locked": False, "verificationPending": False,
            "roles": [{"enumKey": "TR"}], **profile_changes,
        },
    }


class AppackIdentityTests(unittest.TestCase):
    def setUp(self):
        with auth._ADMISSION_LOCK:
            auth._PROVIDER_CALLS.clear()

    def verify(self, data):
        with patch.object(auth, "_whoami", return_value=data):
            return auth.require_appack_occupancy_identity(TOKEN, NOW)

    def test_trainer_identity_overwrites_all_client_authority(self):
        forged = {"requesterId": "victim", "creator": {"id": "victim", "name": "Fake"},
                  "isAppAdministrator": True, "_occupancyIdentityProvider": "platzwart"}
        with patch.object(auth, "_whoami", return_value=identity()):
            result = function_app._authorize_occupancy_write(request("test", TOKEN), forged, NOW)
        self.assertEqual(result["requesterId"], "appack-42")
        self.assertEqual(result["creator"]["name"], "Test Trainer")
        self.assertFalse(result["isAppAdministrator"])
        self.assertEqual(forged["requesterId"], "victim")
        self.assertEqual(result["_occupancyIdentityProvider"], "appack")

    def test_only_current_exact_role_keys_grant_authority(self):
        for role in ["TR", "AA"]:
            self.assertEqual(self.verify(identity(roles=[{"enumKey": role}]))["isAppAdministrator"], role == "AA")
        for roles in [[], [{"enumKey": "M"}], [{"enumKey": "VS"}], [{"enumKey": "T"}],
                      [{"value": "Trainer"}], [{"enumKey": "OTHER", "value": "Trainer"}]]:
            with self.subTest(roles=roles), self.assertRaises(PlatzwartError) as rejected:
                self.verify(identity(roles=roles, requestedRoles=[{"enumKey": "TR"}], roleKeys=["TR"]))
            self.assertEqual(rejected.exception.status_code, 403)

    def test_wrong_app_expired_locked_missing_and_ambiguous_profiles_rejected(self):
        variants = [identity(appId="other"), identity(locked=True), identity(locked=None),
                    identity(verificationPending=True), identity(id=""), identity(id="bad\nid"),
                    identity(roles=None), {**identity(), "appScope": "other"},
                    {**identity(), "expiresAt": NOW.isoformat()},
                    {**identity(), "expiresAt": "2026-08-18T14:00:00"},
                    {**identity(), "expiresAt": None}, {**identity(), "userProfile": None}, {}]
        for data in variants:
            with self.subTest(data=data), self.assertRaises(PlatzwartError):
                self.verify(data)

    def test_provider_epoch_millisecond_date_is_supported(self):
        self.verify({**identity(), "expiresAt": (NOW + timedelta(minutes=10)).timestamp() * 1000})

    def test_malformed_tokens_never_leave_process(self):
        with patch.object(auth, "_whoami") as remote:
            for token in ["", "one.two", "a..c", "a.b.c\r\nx-secret: x", "x" * 16385]:
                with self.assertRaises(PlatzwartError):
                    auth.require_appack_occupancy_identity(token, NOW)
            remote.assert_not_called()

    def test_revoked_role_is_not_cached(self):
        with patch.object(auth, "_whoami", side_effect=[identity(), identity(roles=[])]):
            auth.require_appack_occupancy_identity(TOKEN, NOW)
            with self.assertRaises(PlatzwartError):
                auth.require_appack_occupancy_identity(TOKEN, NOW)

    def test_burst_and_concurrency_rejected_before_provider_and_recover(self):
        with patch.object(auth.time, "monotonic", return_value=100):
            with auth._provider_admission(), auth._provider_admission(), auth._provider_admission():
                with self.assertRaises(PlatzwartError) as rejected:
                    with auth._provider_admission():
                        self.fail("Fourth provider call admitted")
                self.assertEqual(rejected.exception.status_code, 429)
            for _ in range(27):
                with auth._provider_admission():
                    pass
            with patch.object(auth, "_whoami") as remote, self.assertRaises(PlatzwartError):
                auth.require_appack_occupancy_identity(TOKEN, NOW)
            remote.assert_not_called()
        with patch.object(auth.time, "monotonic", return_value=161):
            with auth._provider_admission():
                pass

    def test_transport_uses_only_fixed_provider_and_bounded_nonredirected_read(self):
        response = MagicMock(status_code=200)
        response.__enter__.return_value = response
        response.iter_content.return_value = [json.dumps({"data": {"whoami": identity()}}).encode()]
        with patch.object(auth.requests, "post", return_value=response) as post:
            self.assertEqual(auth._whoami(TOKEN)["userProfile"]["id"], "appack-42")
        self.assertEqual(post.call_args.args, (auth.APPACK_GRAPHQL_URL,))
        self.assertFalse(post.call_args.kwargs["allow_redirects"])
        self.assertEqual(post.call_args.kwargs["timeout"], (3, 8))
        self.assertEqual(post.call_args.kwargs["json"], {"query": auth.WHOAMI_QUERY})
        self.assertNotIn("findOneUserProfile", auth.WHOAMI_QUERY)

    def test_provider_failures_never_expose_credentials_or_grant_access(self):
        for status, raw in [(401, b""), (403, b""), (302, b""), (429, b""), (500, b""),
                            (200, b"not json"), (200, b"x" * 32769),
                            (200, b'{"data":null,"errors":[{"message":"secret"}]}')]:
            response = MagicMock(status_code=status)
            response.__enter__.return_value = response
            response.iter_content.return_value = [raw]
            with self.subTest(status=status, size=len(raw)), patch.object(auth.requests, "post", return_value=response):
                with self.assertRaises(PlatzwartError) as rejected:
                    auth._whoami(TOKEN)
                self.assertNotIn(TOKEN, str(rejected.exception))
                self.assertNotIn("secret", str(rejected.exception))
        with patch.object(auth.requests, "post", side_effect=requests.Timeout("secret " + TOKEN)):
            with self.assertRaises(PlatzwartError) as rejected:
                auth._whoami(TOKEN)
            self.assertEqual(rejected.exception.status_code, 503)
            self.assertNotIn(TOKEN, str(rejected.exception))


class AuthenticatedTrainerFlowTests(unittest.TestCase):
    def setUp(self):
        with auth._ADMISSION_LOCK:
            auth._PROVIDER_CALLS.clear()
        self.store = InMemorySpecialOccupancyStore()
        self.cancellations = InMemoryCancellationStore()
        self.profile = identity()
        patches = [
            patch.object(auth, "_whoami", side_effect=lambda token: copy.deepcopy(self.profile)),
            patch.object(function_app.AzureTableSpecialOccupancyStore, "from_environment", return_value=self.store),
            patch.object(function_app.AzureTableCancellationStore, "from_environment", return_value=self.cancellations),
            patch.dict(function_app.os.environ, {"SSV53_SPECIAL_OCCUPANCY_ENABLED": "true"}),
            patch.object(function_app, "datetime", wraps=datetime),
        ]
        for item in patches:
            mocked = item.start()
            self.addCleanup(item.stop)
        mocked.now.return_value = NOW

    def move(self, event="training:sommer:som-ras-a-do:2026-08-20", **changes):
        return {"action": "move", "commandId": "trainer-move:auth-test", "eventId": event,
                "targetResourceId": "kunstrasen", "confirmation": "TRAINER_BELEGUNG_VERSCHIEBEN",
                "isAppAdministrator": True, "requesterId": "victim", **changes}

    def test_role_only_trainer_moves_real_occurrence_and_preserves_buffers(self):
        response = function_app.ssv53_trainer_occupancies(request("test", TOKEN, self.move()))
        self.assertEqual(response.status_code, 200, response.get_body())
        event = next(iter(self.store.events.values()))
        self.assertTrue(event.suppress_training)
        self.assertEqual(event.moved_by_id, "appack-42")
        self.assertEqual(event.mower_buffer_before_minutes, 30)
        self.assertEqual(event.mower_buffer_after_minutes, 30)

    def test_missing_or_revoked_role_cannot_change_or_read_access(self):
        self.profile["userProfile"]["roles"] = []
        for method in ["GET", "POST"]:
            response = function_app.ssv53_trainer_occupancies(request("test", TOKEN, self.move(), method))
            self.assertEqual(response.status_code, 403)
        self.assertFalse(self.store.events)

    def test_readonly_access_check_has_no_occupancy_or_controller_effect(self):
        with patch.object(function_app.AzureTableSpecialOccupancyStore, "from_environment") as storage:
            response = function_app.ssv53_trainer_occupancies(request("test", TOKEN, method="GET"))
        storage.assert_not_called()
        payload = json.loads(response.get_body())
        self.assertTrue(payload["canManageTrainings"])
        self.assertEqual(payload["creator"]["id"], "appack-42")
        self.assertFalse(payload["isAppAdministrator"])
        self.assertNotIn("email", payload["creator"])

    def test_cancellation_still_keeps_release_delay_and_restore(self):
        data = {"eventId": "training:sommer:som-ras-a-do:2026-08-20", "action": "cancel",
                "confirmation": "TRAINING_FAELLT_AUS"}
        response = function_app.ssv53_training_cancellations(request("test", TOKEN, data))
        self.assertEqual(response.status_code, 200, response.get_body())
        self.assertEqual(json.loads(response.get_body())["mowerReleaseNotBeforeUtc"], (NOW + timedelta(minutes=30)).isoformat())
        data.update(action="restore", confirmation="TRAINING_WIEDER_AKTIV")
        self.assertEqual(function_app.ssv53_training_cancellations(request("test", TOKEN, data)).status_code, 200)

    def test_create_namespace_cannot_overwrite_another_trainers_event(self):
        data = {"commandId": "create:trainer-a", "eventId": "trainer-shared-id", "title": "Training",
                "start": "2026-08-20T02:00:00+02:00", "end": "2026-08-20T03:00:00+02:00",
                "resourceId": "rasen", "confirmation": "TRAINER_BELEGUNG_SPEICHERN"}
        self.assertEqual(function_app.ssv53_trainer_occupancies(request("test", TOKEN, data)).status_code, 200)
        original = next(iter(self.store.events.values()))
        # The same request is idempotent; a second person cannot seize its ID.
        self.assertEqual(function_app.ssv53_trainer_occupancies(request("test", TOKEN, data)).status_code, 200)
        self.assertEqual(len(self.store.events), 1)
        self.profile["userProfile"]["id"] = "another-trainer"
        data.update(overlapConfirmation="UEBERSCHNEIDUNG_TROTZDEM_SPEICHERN")
        self.assertEqual(function_app.ssv53_trainer_occupancies(request("test", TOKEN, data)).status_code, 200)
        self.assertEqual(len(self.store.events), 2)
        self.assertEqual(self.store.get_active(original.event_id).creator_id, "appack-42")
        moved = function_app.ssv53_trainer_occupancies(request("test", TOKEN, self.move("one-off:" + original.event_id)))
        self.assertEqual(moved.status_code, 403)
        delete = {"action": "delete", "eventId": original.event_id, "commandId": "delete:other",
                  "confirmation": "TRAINER_BELEGUNG_LOESCHEN", "isAppAdministrator": True,
                  "requesterId": "appack-42"}
        self.assertEqual(function_app.ssv53_trainer_occupancies(request("test", TOKEN, delete)).status_code, 403)
