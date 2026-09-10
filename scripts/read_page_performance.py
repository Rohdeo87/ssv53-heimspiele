"""Aggregate read-only latency evidence for the fixed SSV53 production app."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--minutes", type=int, default=60)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not 1 <= args.minutes <= 1440:
        parser.error("minutes must be between 1 and 1440")
    where = f"timestamp > ago({args.minutes}m)"
    queries = {
        "requests": f'requests | where {where} and name has "platzwart_status" '
                    '| extend view=iff(url has "view=live", "live", "full") '
                    '| summarize samples=count(), p50_ms=percentile(duration,50), '
                    'p95_ms=percentile(duration,95), max_ms=max(duration) by view,resultCode',
        "server_timings": f'traces | where {where} and message startswith "SSV53_PLATZWART_PAGE_TIMING " '
                          '| parse message with * "view=" view " duration_ms=" ms '
                          '| summarize samples=count(), p50_ms=percentile(todouble(ms),50), '
                          'p95_ms=percentile(todouble(ms),95), max_ms=max(todouble(ms)) by view',
    }
    result = {"retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
              "read_only": True, "minutes": args.minutes}
    for key, query in queries.items():
        process = subprocess.run([
            r"C:\Program Files\Microsoft SDKs\Azure\CLI2\wbin\az.cmd", "monitor", "app-insights", "query",
            "-g", "rg-ssv53-platzpflege-prod", "--app", "appi-ssv53platzpflege-prod-q7kbw54s",
            "--analytics-query", query, "--offset", "1d", "-o", "json", "--only-show-errors",
        ], capture_output=True, check=True, timeout=90)
        table = json.loads(process.stdout.decode("utf-8-sig"))["tables"][0]
        result[key] = [dict(zip([c["name"] for c in table["columns"]], row)) for row in table["rows"]]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
