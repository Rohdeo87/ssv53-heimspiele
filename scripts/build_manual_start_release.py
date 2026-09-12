"""Build only the six reviewed fixes over the verified Sep 12 live package."""
from scripts import build_irrigation_reliability_release as release

release.BASE_SHA256 = "d374016b808a6171f7fb81f98f5957eef146c96ba8a586001e7c750de3d9ae10"
release.PATCHED = frozenset({
    "mower/device_send_guard.py", "mower/full_failsafe.py",
    "mower/irrigation_park_hold.py", "mower/manual_control_api.py",
    "mower/start_dispatch_guard.py", "mower/start_recovery.py",
})
release.ADDED = frozenset()

if __name__ == "__main__":
    release.main()
