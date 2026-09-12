"""Verify the installed release and UI before enabling only automatic takeover.

No device endpoint is called. Unrelated settings and credentials stay in memory.
Without --activate this script performs only read-only verification.
"""
from datetime import datetime, timezone
import argparse
import hashlib
import json
from pathlib import Path
import re

import requests
from activate_onsite_dock_release import PUBLIC, settings
from read_grounds_installation import APP, GROUP, HOST, az

FLAG = "MOWER_AUTOMATIC_TAKEOVER_ENABLED"
REQUIRED = ("IRRIGATION_ONSITE_DOCK_CONFIRMATION_ENABLED", "IRRIGATION_TERMINAL_CLEANUP_ENABLED",
            "ENABLE_MANUAL_SESSIONS", "ENABLE_OPERATOR_SAFETY_GUARD")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--activate", action="store_true")
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--template-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    for expected in (args.manifest_sha256, args.template_sha256):
        if not re.fullmatch(r"[a-f0-9]{64}", expected):
            raise ValueError("Expected lowercase SHA-256")
    source = Path("appack-platzwart-dashboard.html").read_text(encoding="utf-8")
    if hashlib.sha256(source.encode()).hexdigest() != args.template_sha256:
        raise RuntimeError("Local template differs from approved template")
    response = requests.get(PUBLIC, timeout=(5, 40), allow_redirects=False,
                            headers={"Cache-Control": "no-cache"})
    response.raise_for_status()
    public = response.content.decode("utf-8").replace("\r\n", "\n")
    lines = source.splitlines()
    verified = []
    for name in ("dashboardMessage", "manualControlPrepare", "irrigationStartAllowed"):
        start = next(i for i, line in enumerate(lines) if line.startswith("    function " + name + "("))
        end = start
        if lines[start].rstrip().endswith("{"):
            end = next(i for i in range(start + 1, len(lines)) if lines[i].rstrip() == "    }")
        if "\n".join(lines[start:end + 1]) not in public:
            raise RuntimeError("Published function differs: " + name)
        verified.append(name)
    handler = next(line for line in lines if 'document.getElementById("confirm-go").onclick=' in line)
    if handler not in public:
        raise RuntimeError("Published confirmation handler differs")
    key = az("functionapp", "keys", "list", "-g", GROUP, "-n", APP)["masterKey"]
    manifest = requests.get(HOST + "/admin/vfs/home/site/wwwroot/package-manifest.json",
                            headers={"x-functions-key": key}, timeout=(5, 30))
    manifest.raise_for_status()
    if hashlib.sha256(manifest.content).hexdigest() != args.manifest_sha256:
        raise RuntimeError("Installed release differs")
    before = settings()
    if not all(str(before.get(k, {}).get("value")).lower() == "true" for k in REQUIRED):
        raise RuntimeError("Required existing safety flags are not enabled")
    report = {"at_utc": datetime.now(timezone.utc).isoformat(), "function_app": APP,
              "activation_requested": args.activate, "device_commands_requested": False,
              "manifest_sha256": args.manifest_sha256, "template_sha256": args.template_sha256,
              "public_url": PUBLIC, "public_functions_verified": verified,
              "public_confirmation_handler_matches": True,
              "flag_before": before.get(FLAG, {}).get("value")}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if args.activate:
        az("functionapp", "config", "appsettings", "set", "-g", GROUP, "-n", APP,
           "--settings", FLAG + "=true")
    after = settings()
    changed = sorted(k for k in before.keys() | after.keys() if before.get(k) != after.get(k))
    report.update({"verified_at_utc": datetime.now(timezone.utc).isoformat(),
                   "changed_setting_names": changed,
                   "unrelated_settings_unchanged": not (set(changed) - {FLAG}),
                   "flag_after": after.get(FLAG, {}).get("value"),
                   "required_flags_after": {k: after.get(k, {}).get("value") for k in REQUIRED}})
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if set(changed) - {FLAG}:
        raise RuntimeError("Unexpected unrelated settings change; inspect report")
    if args.activate and after.get(FLAG, {}).get("value") != "true":
        raise RuntimeError("Flag activation not verified")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
