from __future__ import annotations

import json
from unittest.mock import patch

from mower.husqvarna_statistics_actions import reset_cutting_blade_usage_time


def test_reset_blade_usage_uses_only_the_dedicated_husqvarna_action():
    captured = {}

    class Response:
        status = 202

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self):
            return b""

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["method"] = request.method
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return Response()

    with patch(
        "mower.husqvarna_statistics_actions.get_access_token",
        return_value="token",
    ), patch(
        "mower.husqvarna_statistics_actions.urlopen",
        side_effect=fake_urlopen,
    ):
        result = reset_cutting_blade_usage_time("client", "secret", "mower-1")

    assert captured == {
        "url": "https://api.amc.husqvarna.dev/v1/mowers/mower-1/actions",
        "method": "POST",
        "body": {"data": {"type": "ResetCuttingBladeUsageTime"}},
    }
    assert result["accepted"] is True
