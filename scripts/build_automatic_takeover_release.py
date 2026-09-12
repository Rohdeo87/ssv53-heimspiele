"""Bounded takeover patch over the verified 12 September dock release."""
from scripts import build_irrigation_reliability_release as release

release.BASE_SHA256 = "c5bb19a88021b0276166038725ca95e24368df3d612e9ca6e6ba833ac9519d79"
release.PATCHED = frozenset({"mower/full_failsafe.py", "mower/state.py", "mower/automatic_takeover.py"})
release.ADDED = frozenset({"mower/automatic_takeover.py"})

if __name__ == "__main__":
    release.main()
