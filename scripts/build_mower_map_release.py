"""Display-only GPS patch over the verified charging-calibration archive."""
from scripts import build_irrigation_reliability_release as release

release.BASE_SHA256 = '62acc594a48cc4b5ac8edc94ad4c7598207da9ead2f57dbd6e9bc4b65dbd9638'
release.PATCHED = frozenset({'mower/husqvarna.py','mower/dry_run.py','platzwart_console.py'})
release.ADDED = frozenset()

if __name__ == '__main__':
    release.main()
