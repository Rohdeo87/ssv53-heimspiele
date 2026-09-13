"""Read-only proof of the published map template and actual Azure function indexing."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import requests

from read_grounds_installation import az, APP, GROUP, HOST


def main():
    source = Path("appack-platzwart-dashboard.html").read_text(encoding="utf-8")
    assert source == Path("appack-platzwart-dashboard.txt").read_text(encoding="utf-8")
    response = requests.get("https://appack.de/rest-api/drender/6a86ab6c4b3c829dd60de9b7",
                            headers={"Cache-Control": "no-cache"}, timeout=(5, 40))
    response.raise_for_status()
    public = response.content.decode("utf-8").replace("\r\n", "\n")
    lines = source.splitlines()
    verified = []
    for name in ("pfMapPoint", "pfMapCovered", "pfLoadMapAssets", "pfMapCenter",
                 "pfRetryMap", "pfMountMap", "pfRenderMap"):
        start = next(i for i, line in enumerate(lines) if line.startswith("    function " + name + "("))
        end = next(i for i in range(start + 1, len(lines)) if lines[i] == "    }")
        assert "\n".join(lines[start:end + 1]) in public, name
        verified.append(name)
    assert "© Geoportal Berlin, dl-de/by-2-0" in public
    assert "Darstellung um Mähersymbol ergänzt." in public
    key = az("functionapp", "keys", "list", "-g", GROUP, "-n", APP)["masterKey"]
    functions = requests.get(HOST + "/admin/functions", headers={"x-functions-key": key}, timeout=(5, 30))
    functions.raise_for_status()
    names = sorted(item["name"] for item in functions.json())
    assert len(names) == 16, names
    query = ('traces | where timestamp > ago(10m) and message startswith "SSV53_CONTROL_CYCLE " '
             '| extend p=parse_json(substring(message,20)) '
             '| project timestamp, manifest=tostring(p.build_provenance.package_manifest_sha256), '
             'decision=tostring(p.decision_code), command_sent=tobool(p.command_sent) '
             '| order by timestamp desc | take 5')
    table = az("monitor", "app-insights", "query", "-g", GROUP,
               "--app", "appi-ssv53platzpflege-prod-q7kbw54s", "--analytics-query", query,
               "--offset", "1h")["tables"][0]
    rows = [dict(zip([c["name"] for c in table["columns"]], row)) for row in table["rows"]]
    report = {"at_utc": datetime.now(timezone.utc).isoformat(), "read_only": True,
              "template_sha256_lf": hashlib.sha256(source.encode()).hexdigest(),
              "public_map_functions_match": verified, "license_attribution_verified": True,
              "runtime_functions": names, "recent_existing_control_cycles": rows,
              "authenticated_map_response_tested": False, "physical_position_accuracy_tested": False}
    Path("docs/ui-2026-09-12/mower-map/public-verification-local.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    public_report = {k: v for k, v in report.items()
                     if k not in {"runtime_functions", "recent_existing_control_cycles"}}
    public_report["runtime_function_count"] = len(names)
    public_report["existing_timer_observed_after_deployment"] = bool(rows)
    public_report["publication_scope"] = "Verification summary only; operational records retained locally."
    Path("docs/ui-2026-09-12/mower-map/public-verification.json").write_text(
        json.dumps(public_report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(public_report, ensure_ascii=False))


if __name__ == "__main__":
    main()
