"""Statistics-only patch over the verified mower-map archive."""
from scripts import build_irrigation_reliability_release as release

release.BASE_SHA256 = "8c66deec91852654d748a496507ec97f547ec51afbaaebda277ec7a2fe928eed"
release.PATCHED = frozenset({"daily_safety_report.py", "mower/irrigation_journal.py",
                             "mower/irrigation_duration.py"})
release.ADDED = frozenset({"mower/irrigation_duration.py"})

if __name__ == "__main__":
    release.main()
