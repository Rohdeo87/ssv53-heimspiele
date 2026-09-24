"""One-shot source check. No deployment or Appack changes."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from .collector import Fetcher, SPORTS, collect


def main() -> int:
    parser = argparse.ArgumentParser(description="SSV53 Handball-/Volleyball-Quellencheck")
    parser.add_argument("--output", type=Path, default=Path("results-data"))
    parser.add_argument("--sport", choices=SPORTS)
    parser.add_argument("--season-start", type=int)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    failed = False
    for sport in ([args.sport] if args.sport else SPORTS):
        path = args.output / f"{sport}.json"
        old = json.loads(path.read_text("utf-8")) if path.exists() else None
        fetcher = Fetcher()
        try:
            payload = collect(sport, fetcher, old, season_start=args.season_start)
        finally:
            fetcher.close()
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), "utf-8")
        temporary.replace(path)
        stale = payload["stale"]
        failed |= stale
        print(f"{sport}: {'PRÜFUNG ERFORDERLICH' if stale else 'OK'}; {len(payload['teams'])} Wettbewerbe; {fetcher.request_count} Requests")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
