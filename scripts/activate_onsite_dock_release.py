"""Activate only the two explicitly approved flags after installed/UI verification.

No device endpoint is called. Credentials and unrelated setting values stay in memory.
Without --activate this script only verifies and records the current release.
"""
from datetime import datetime, timezone
import argparse
import hashlib
import json
from pathlib import Path

import requests
from read_grounds_installation import APP, GROUP, HOST, az

MANIFEST = "88e418f5c967991d6f842ad732e26ab223ecfd6be24709140816e5cd77d26264"
TEMPLATE = "5d562aaf7644e1aac3c15e0dd76e453c02a91e247465c0605b7d4d5347b48e31"
FLAGS = ("IRRIGATION_ONSITE_DOCK_CONFIRMATION_ENABLED", "IRRIGATION_TERMINAL_CLEANUP_ENABLED")
PUBLIC = "https://appack.de/rest-api/drender/6a86ab6c4b3c829dd60de9b7"


def settings():
    return {r["name"]: {"value": r.get("value"), "slotSetting": r.get("slotSetting")}
            for r in az("functionapp", "config", "appsettings", "list", "-g", GROUP, "-n", APP)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--activate", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    source = Path("appack-platzwart-dashboard.html").read_text(encoding="utf-8").replace("\r\n", "\n")
    if hashlib.sha256(source.encode()).hexdigest() != TEMPLATE:
        raise RuntimeError("Local template differs from approved template")
    response = requests.get(PUBLIC, timeout=(5, 40), allow_redirects=False,
                            headers={"Cache-Control": "no-cache"})
    response.raise_for_status()
    public = response.content.decode("utf-8").replace("\r\n", "\n")
    lines = source.splitlines()
    # Compare the complete changed functions and confirmation UI; CMS expands profile data.
    names = ("irrigationDockConfirmation", "irrigationStartAllowed", "clearOnsiteDockConfirmation",
             "prepareOnsiteDockPayload", "openAction", "manualControlPrepare")
    verified = []
    for name in names:
        start = next(i for i, line in enumerate(lines) if line.startswith("    function " + name + "("))
        end = start
        if lines[start].rstrip().endswith("{"):
            end = next(i for i in range(start + 1, len(lines)) if lines[i].rstrip() == "    }")
        body = "\n".join(lines[start:end + 1])
        if body not in public:
            raise RuntimeError("Published function differs: " + name)
        verified.append(name)
    handler = next(line for line in lines if 'document.getElementById("confirm-go").onclick=' in line)
    checkbox = next(line for line in lines if '<dialog id="confirm-dialog">' in line)
    if handler not in public or checkbox not in public:
        raise RuntimeError("Published confirmation handler or checkbox differs")
    key = az("functionapp", "keys", "list", "-g", GROUP, "-n", APP)["masterKey"]
    manifest = requests.get(HOST + "/admin/vfs/home/site/wwwroot/package-manifest.json",
                            headers={"x-functions-key": key}, timeout=(5, 30))
    manifest.raise_for_status()
    if hashlib.sha256(manifest.content).hexdigest() != MANIFEST:
        raise RuntimeError("Installed release differs")
    before = settings()
    report = {"at_utc": datetime.now(timezone.utc).isoformat(), "function_app": APP,
              "activation_requested": args.activate, "device_commands_requested": False,
              "manifest_sha256": MANIFEST, "template_sha256": TEMPLATE,
              "public_url": PUBLIC, "public_functions_verified": verified,
              "public_confirmation_handler_and_checkbox_match": True,
              "flags_before": {k: before.get(k, {}).get("value") for k in FLAGS}}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if args.activate:
        az("functionapp", "config", "appsettings", "set", "-g", GROUP, "-n", APP,
           "--settings", *(k + "=true" for k in FLAGS))
    after = settings()
    changed = sorted(k for k in before.keys() | after.keys() if before.get(k) != after.get(k))
    report.update({"verified_at_utc": datetime.now(timezone.utc).isoformat(),
                   "changed_setting_names": changed,
                   "unrelated_settings_unchanged": not (set(changed) - set(FLAGS)),
                   "flags_after": {k: after.get(k, {}).get("value") for k in FLAGS}})
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if set(changed) - set(FLAGS):
        raise RuntimeError("Unexpected unrelated settings change; inspect report")
    if args.activate and not all(after.get(k, {}).get("value") == "true" for k in FLAGS):
        raise RuntimeError("Flag activation not verified")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
