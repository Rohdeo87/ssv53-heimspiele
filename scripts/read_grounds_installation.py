"""Read-only proof for the named SSV53 Function App; credentials stay in memory."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import subprocess
import zipfile

import requests


AZ = r"C:\Program Files\Microsoft SDKs\Azure\CLI2\wbin\az.cmd"
APP = "func-ssv53platzpflege-prod-q7kbw54s"
GROUP = "rg-ssv53-platzpflege-prod"
HOST = f"https://{APP}.azurewebsites.net"
FLAGS = (
    "IRRIGATION_CONFIRMED_PARK_HOLD_ENABLED",
    "CONTROL_MODE", "ENABLE_LIVE_READS", "ENABLE_PARK_COMMANDS",
    "ENABLE_START_COMMANDS", "ENABLE_IRRIGATION_COMMANDS", "ENABLE_MANUAL_SESSIONS",
    "ENABLE_OPERATOR_SAFETY_GUARD", "FULL_MOWER_CONFIRMATION", "FULL_FAILSAFE_CONFIRMATION",
    "OPERATOR_CONTROL_CONFIRMATION", "COORDINATION_EXECUTION_ENABLED",
    "COORDINATION_EXECUTION_CONFIRMATION", "SHARED_TRAINING_MODE",
    "WINTER_TRAINING_CONTROL_ENABLED", "ENABLE_OPERATOR_CUTTING_HEIGHT_COMMANDS",
    "MAX_AUTOMATIC_START_MINUTES", "SSV53_SPECIAL_OCCUPANCY_ENABLED",
)


def az(*args):
    result = subprocess.run([AZ, *args, "-o", "json", "--only-show-errors"],
                            capture_output=True, check=True, timeout=90)
    try:
        return json.loads(result.stdout.decode("utf-8"))
    except UnicodeDecodeError:
        return json.loads(result.stdout.decode("cp1252"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--package", type=Path)
    parser.add_argument("--compare-settings", type=Path)
    parser.add_argument("--expected-function-count", type=int, default=15)
    args = parser.parse_args()
    settings = {item["name"]: item.get("value") for item in
                az("functionapp", "config", "appsettings", "list", "-g", GROUP, "-n", APP)
                if item["name"] in FLAGS}
    functions = az("functionapp", "function", "list", "-g", GROUP, "-n", APP)
    key = az("functionapp", "keys", "list", "-g", GROUP, "-n", APP)["masterKey"]

    def read(path):
        response = requests.get(HOST + path, headers={"x-functions-key": key},
                                timeout=(5, 20), allow_redirects=False)
        response.raise_for_status()
        if response.status_code != 200:
            raise RuntimeError("Unexpected non-200 installation read")
        return response.content

    manifest = read("/admin/vfs/home/site/wwwroot/package-manifest.json")
    host = json.loads(read("/admin/host/status"))
    report = {
        "at_utc": datetime.now(timezone.utc).isoformat(), "read_only": True,
        "function_app": APP, "host_state": host.get("state"),
        "functions": sorted(item["name"].split("/")[-1] for item in functions),
        "settings": settings, "manifest_sha256": hashlib.sha256(manifest).hexdigest(),
        "device_execution_tested": False,
    }
    if args.compare_settings:
        previous = json.loads(args.compare_settings.read_text(encoding="utf-8"))["settings"]
        report["settings_unchanged"] = settings == previous
        if settings != previous:
            raise RuntimeError("Live flags changed; installation proof rejected")
    if args.package:
        with zipfile.ZipFile(args.package) as archive:
            expected = {name: archive.read(name) for name in archive.namelist()}
        if expected["package-manifest.json"] != manifest:
            raise RuntimeError("Installed package manifest differs")
        def verify(name):
            path = PurePosixPath(name)
            if path.is_absolute() or ".." in path.parts or "\\" in name:
                raise RuntimeError("Unsafe package path")
            content = read("/admin/vfs/home/site/wwwroot/" + name)
            if content != expected[name]:
                raise RuntimeError("Installed source differs: " + name)
            return {"path": name, "sha256": hashlib.sha256(content).hexdigest()}
        with ThreadPoolExecutor(max_workers=2) as pool:
            report["installed_files"] = list(pool.map(verify, sorted(expected)))
        report["all_package_files_match"] = True
        report["package_sha256"] = hashlib.sha256(args.package.read_bytes()).hexdigest()
        if host.get("state") != "Running" or len(functions) != args.expected_function_count:
            raise RuntimeError("Running host or expected function count differs")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "host_state": report["host_state"],
                      "function_count": len(functions), "manifest": report["manifest_sha256"],
                      "verified_files": len(report.get("installed_files", [])),
                      "settings_unchanged": report.get("settings_unchanged")}))


if __name__ == "__main__":
    main()
