"""Build this incident's bounded patch from the verified installed package.

No service access, environment changes or deployment. Unchanged sources must
match the chosen Git commit apart from line endings before preserving their
installed bytes. Changed and added files come from Git, never an uncommitted edit.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import zipfile

BASE_SHA256 = "27c8a826b12e5b9efd0f6414d0d2efe861cbd42bfd5972546033a3ee944aca9c"
PATCHED = frozenset({"mower/full_failsafe.py", "mower/irrigation_recovery.py",
                     "mower/state.py", "mower/irrigation_park_hold.py"})
ADDED = frozenset({"mower/irrigation_park_hold.py"})


def build(repository: Path, base: Path, commit: str, output: Path) -> dict:
    def git(*args):
        return subprocess.check_output(["git", "-C", str(repository), *args])

    source_commit = git("rev-parse", "--verify", f"{commit}^{{commit}}").decode().strip()
    if hashlib.sha256(base.read_bytes()).hexdigest() != BASE_SHA256:
        raise ValueError("Base package differs from the verified installed release")
    builder_path = "scripts/build_azure_full_failsafe_package.py"
    builder = types.ModuleType("committed_package_builder")
    exec(compile(git("show", f"{source_commit}:{builder_path}"), builder_path, "exec"), builder.__dict__)
    with zipfile.ZipFile(base) as archive:
        previous = {name: archive.read(name) for name in archive.namelist()}
    if set(previous) != (set(builder.REQUIRED_FILES) - ADDED) | {"package-manifest.json"}:
        raise ValueError("Unexpected base package inventory")

    with tempfile.TemporaryDirectory(prefix="ssv53-irrigation-patch-") as temporary:
        stage = Path(temporary)
        for name in builder.REQUIRED_FILES:
            builder._safe_path(name)
            canonical = git("show", f"{source_commit}:{name}")
            if name not in PATCHED and canonical.replace(b"\r\n", b"\n") != previous[name].replace(b"\r\n", b"\n"):
                raise ValueError(f"Unrelated source changed: {name}")
            target = stage / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(canonical if name in PATCHED else previous[name])
        result = builder.build_package(stage, output)
        with zipfile.ZipFile(output) as archive:
            current = {name: archive.read(name) for name in archive.namelist()}
        changed = sorted(name for name in set(previous) | set(current) if previous.get(name) != current.get(name))
        if set(changed) != PATCHED | {"package-manifest.json"}:
            raise ValueError(f"Unexpected changed package files: {changed}")
        (stage / "package-manifest.json").write_bytes(current["package-manifest.json"])
        probe = """
import json, socket
from pathlib import Path
def denied(*a, **kw):
    raise AssertionError('Network forbidden in release validation')
socket.socket.connect = denied
socket.socket.connect_ex = denied
socket.socket.sendto = denied
socket.create_connection = denied
socket.getaddrinfo = denied
import function_app
from mower.build_provenance import inspect_installed_package
proof = inspect_installed_package(Path.cwd())
assert proof['manifest_files_verified'], proof
count = len(function_app.app.get_functions())
assert count == 16, count
print(json.dumps({'functions': count, 'manifest_files_verified': True, 'network_allowed': False}))
"""
        environment = {k: v for k, v in os.environ.items()
                       if k.upper() in {"SYSTEMROOT", "WINDIR", "TEMP", "TMP"}}
        environment.update(PYTHONDONTWRITEBYTECODE="1", PYTHONUTF8="1")
        imported = subprocess.run([sys.executable, "-X", "utf8", "-c", probe],
                                  cwd=stage, env=environment, check=True,
                                  capture_output=True, text=True)
    return {
        "source_commit": source_commit,
        "base_package_sha256": BASE_SHA256,
        "package_sha256": result["sha256"],
        "manifest_sha256": hashlib.sha256(current["package-manifest.json"]).hexdigest(),
        "package_entries": len(current),
        "changed_files": changed,
        "changed_source_hashes": {name: hashlib.sha256(current[name]).hexdigest() for name in sorted(PATCHED)},
        "unchanged_source_content_verified_against_commit": True,
        "unchanged_package_bytes_preserved": True,
        "offline_import": json.loads(imported.stdout),
        "deployed": False, "device_commands_sent": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, default=Path("dist/return-status-release.zip"))
    parser.add_argument("--commit", default="HEAD")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    report = build(Path(__file__).resolve().parents[1], args.base, args.commit, args.output.resolve())
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
