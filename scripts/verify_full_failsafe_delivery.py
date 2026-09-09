"""Build and import exact committed source bytes locally; never deploy anything."""
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


def verify(repository: Path, commit: str, output: Path) -> dict:
    def git(*args: str) -> bytes:
        return subprocess.check_output(["git", "-C", str(repository), *args])

    source_commit = git("rev-parse", "--verify", f"{commit}^{{commit}}").decode().strip()
    builder_path = "scripts/build_azure_full_failsafe_package.py"
    builder_bytes = git("show", f"{source_commit}:{builder_path}")
    builder = types.ModuleType("exact_committed_builder")
    exec(compile(builder_bytes, builder_path, "exec"), builder.__dict__)
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="ssv53-source-proof-") as temporary:
        root = Path(temporary)
        sources = root / "source"
        canonical = {}
        for name in builder.REQUIRED_FILES:
            builder._safe_path(name)
            content = git("show", f"{source_commit}:{name}")
            canonical[name] = content
            target = sources / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        builder.build_package(sources, output)
        extracted = root / "extracted"
        with zipfile.ZipFile(output) as archive:
            if set(archive.namelist()) != {*canonical, "package-manifest.json"}:
                raise RuntimeError("Package inventory differs from committed sources")
            for name, content in canonical.items():
                if archive.read(name) != content:
                    raise RuntimeError(f"Package bytes differ: {name}")
            raw_manifest = archive.read("package-manifest.json")
            manifest = json.loads(raw_manifest)
            archive.extractall(extracted)
        # A clean interpreter, isolated directory and allowlisted environment
        # prove imports without developer credentials or access to any service.
        probe = """
import json
import socket
from pathlib import Path
def denied(*args, **kwargs):
    raise AssertionError('Network is forbidden during package verification')
socket.socket.connect = denied
socket.socket.connect_ex = denied
socket.socket.sendto = denied
socket.create_connection = denied
socket.getaddrinfo = denied
import function_app
from mower.build_provenance import inspect_installed_package
proof = inspect_installed_package(Path.cwd())
assert proof['manifest_files_verified'], proof
print(json.dumps({'imported': True,
    'registered_functions': len(function_app.app.get_functions()),
    'network_allowed': False, 'provenance': proof}))
"""
        environment = {key: value for key, value in os.environ.items()
                       if key.upper() in {"SYSTEMROOT", "WINDIR", "TEMP", "TMP"}}
        environment.update(PYTHONDONTWRITEBYTECODE="1", PYTHONUTF8="1")
        completed = subprocess.run(
            [sys.executable, "-X", "utf8", "-c", probe], cwd=extracted,
            env=environment, check=True, capture_output=True, text=True,
        )
        offline_import = json.loads(completed.stdout)
    return {
        "source_commit": source_commit,
        "builder_sha256": hashlib.sha256(builder_bytes).hexdigest(),
        "package_path": output.relative_to(repository.resolve()).as_posix(),
        "package_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
        "package_entries": len(canonical) + 1,
        "canonical_git_files_verified": len(canonical),
        "package_manifest_sha256": hashlib.sha256(raw_manifest).hexdigest(),
        "source_files": manifest["files"],
        "offline_import": offline_import,
        "release": {"software_deployed": False, "commands_sent": False,
                    "cms_published": False, "shadow_observed": False,
                    "pilot_observed": False, "coordination_default_enabled": False},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", default="HEAD")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    repository = Path(__file__).resolve().parents[1]
    report = verify(repository, args.commit, args.output)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: report[key] for key in
                     ("source_commit", "package_sha256", "canonical_git_files_verified", "offline_import")}))


if __name__ == "__main__":
    main()
