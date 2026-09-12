"""Build the approved dock patch over the byte-verified installed release."""
from scripts import build_irrigation_reliability_release as release

release.BASE_SHA256 = "f16fd90869b4a79564dc6ab74ee5607554992eba35e592a5a660bb9eebb8a627"
release.PATCHED = frozenset({
    "mower/full_failsafe.py", "mower/state.py", "platzwart_console.py",
    "mower/onsite_dock_proof.py",
})
release.ADDED = frozenset({"mower/onsite_dock_proof.py"})

if __name__ == "__main__":
    release.main()
