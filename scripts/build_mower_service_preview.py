"""Build a synthetic mobile review of the winter switch, with all network blocked."""
from copy import deepcopy
from pathlib import Path
import argparse

from scripts.build_audit_preview import build
from scripts.build_manual_control_preview import fixture


def build_service_preview(output: Path):
    data = deepcopy(fixture)
    data["generatedAt"] = "2026-11-16T10:00:00Z"
    data["mowerService"] = {
        "available": True, "active": True, "reason": "WINTER", "label": "Winterpause",
        "since": "2026-11-16T09:00:00Z", "revision": 1,
    }
    data["mower"].update(connected=False, telemetryFresh=False, activity="PARKED_IN_CS", mode="HOME")
    data["automation"] = {"continuousMowingOwned": False}
    data["manualControl"].update(status="OUT_OF_SERVICE", canStart=False, canPark=False,
        canResume=False, title="Mäher außer Betrieb", message="Winterpause ist eingeschaltet.")
    data["actionCapabilities"]["SET_MOWER_SERVICE"] = {"available": True}
    data["coordination"]["blockers"] = [{"code": "MOWER_SERVICE_ACTIVE"}, {"code": "MOWER_TELEMETRY"}]
    data["occupancy"].update(current=None, upcoming=[])
    for action in ("PARK_MOWER", "START_MOWING", "SET_CUTTING_HEIGHT", "RESET_BLADE_USAGE"):
        data["actionCapabilities"][action] = {"available": False, "reason": "MOWER_SERVICE_ACTIVE"}
    build(output=output, fixture=data)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("/tmp/ssv53-service-preview"))
    build_service_preview(parser.parse_args().output)
