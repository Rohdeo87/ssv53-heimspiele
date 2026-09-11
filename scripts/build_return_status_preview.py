"""Reconstruct display scenarios from recorded controller fields; no live calls."""
from copy import deepcopy
import json
from pathlib import Path
from scripts.build_audit_preview import build
from scripts.build_wunschdesign_preview import fixtures

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "docs/ui-2026-09-11/return-status"
OUT = ROOT / "dist/return-status-replay"


def main():
    recorded = json.loads((EVIDENCE / "controller-observations.json").read_text(encoding="utf-8"))
    payloads = {}
    for index, name in [(2, "homeward"), (3, "docked"), (5, "training-wait")]:
        row = recorded["observations"][index]
        s = deepcopy(fixtures()["charging-unknown"])
        s["generatedAt"] = row["at"]
        mower = row["mower"]
        s["mower"].update(activity=mower["activity"], state=mower["state"], mode=mower["mode"],
                          batteryPercent=mower["battery_percent"], statusTimestamp=mower["status_timestamp_ms"],
                          connected=mower["connected"], errorCode=mower["error_code"], restartBatteryPercent=90)
        s["protection"] = {"automaticStartEnabled": True}
        s["automation"].update(continuousMowingOwned=True, irrigationPhase=None)
        s["coordination"].update(dryUntil=row["water"]["release"]["dry_until_utc"],
                                  releaseNotBefore=row["water"]["release"]["release_at_utc"],
                                  telemetryConfirmed=row["water"]["release"]["telemetry_confirmed"],
                                  dryingReason="IRRIGATION_END", blockers=[], chargingEndEstimate=None)
        s["irrigation"]["safety"] = row["water"]["safety"]
        s["occupancy"] = {"available": True, "current": row["plan"]["blocked_now"],
                          "parking": row["plan"]["parking_block"], "next": row["plan"]["next_block"],
                          "safeWindows": row["plan"]["safe_mowing_windows"]}
        payloads[name] = s
    build(output=OUT, fixture=payloads["homeward"])
    page = OUT / "appack-preview.html"
    html = page.read_text(encoding="utf-8")
    html = html.replace("window.auditFixture=", "window.pfFixtures=" + json.dumps(payloads, ensure_ascii=False) + ";\nwindow.auditFixture=", 1)
    html = html.replace("return {ok:true,status:200,json:async()=>window.auditFixture};", "return {ok:true,status:200,json:async()=>window.pfFixtures[location.hash.slice(1)]||window.auditFixture};")
    page.write_text(html, encoding="utf-8")
    print("Reconstruction only; permissions and nonessential fixture values are synthetic.")


if __name__ == "__main__":
    main()
