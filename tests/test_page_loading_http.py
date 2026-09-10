import json
from unittest.mock import patch

import azure.functions as func
import pytest

import function_app
from platzwart_console import PlatzwartError


@pytest.mark.parametrize("params,kwargs", [({}, {}), ({"view": "live"}, {"include_details": False})])
def test_status_variant_remains_authenticated_and_uncached(params, kwargs):
    request = func.HttpRequest(method="GET", url="https://example.test/api/platzwart/status",
                               headers={"Authorization": "Bearer synthetic-test-token"}, params=params, body=b"")
    with patch.object(function_app, "require_platzwart_session", return_value={}) as auth, \
            patch.object(function_app, "platzwart_live_status", return_value={"controlsAvailable": False}) as read:
        response = function_app.ssv53_platzwart_status(request)
    auth.assert_called_once()
    assert read.call_args.kwargs == kwargs
    assert json.loads(response.get_body())["controlsAvailable"] is False
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Server-Timing"].startswith("status;dur=")


def test_fast_query_parameter_never_bypasses_authentication():
    request = func.HttpRequest(method="GET", url="https://example.test/api/platzwart/status",
                               headers={"Authorization": "Bearer synthetic-test-token"}, params={"view": "live"}, body=b"")
    with patch.object(function_app, "require_platzwart_session",
                      side_effect=PlatzwartError("SESSION_INVALID", "Anmeldung erforderlich", 401)), \
            patch.object(function_app, "platzwart_live_status") as read:
        response = function_app.ssv53_platzwart_status(request)
    assert response.status_code == 401
    read.assert_not_called()
