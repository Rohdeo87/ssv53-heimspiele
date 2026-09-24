"""Build an offline, reviewable results ZIP from an explicitly pinned release ZIP.

No Azure, GitHub, device, or CMS writes. The existing release is never modified.
"""
from __future__ import annotations

import argparse
import ast
import copy
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

if __package__:
    from .prepare_integration import HERE, patch_function
else:
    from prepare_integration import HERE, patch_function


RESULT_FILES = (
    "integrations/results/results_blueprint.py",
    "integrations/results/ssv_results/__init__.py",
    "integrations/results/ssv_results/__main__.py",
    "integrations/results/ssv_results/collector.py",
    "integrations/results/ssv_results/parsers.py",
)
REQUIRED_BASE = {"function_app.py", "mower/build_provenance.py", "host.json",
                 "requirements.txt", "mower/controller.py"}
MAX_FILES = 250
MAX_FILE_BYTES = 5_000_000
MAX_TOTAL_BYTES = 30_000_000
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
ZIP_TIMESTAMP = (2026, 1, 1, 0, 0, 0)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def checked_name(name: str) -> str:
    if not name or "\\" in name or ":" in name or "\x00" in name:
        raise ValueError("Unsafe ZIP path")
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or path.as_posix() != name:
        raise ValueError("Unsafe ZIP path")
    return name


def read_verified_base(base_zip: Path, expected_sha256: str) -> tuple[dict[str, bytes], dict]:
    if not SHA256.fullmatch(expected_sha256):
        raise ValueError("Explicit lowercase base ZIP SHA-256 required")
    if base_zip.stat().st_size > MAX_TOTAL_BYTES:
        raise ValueError("Base ZIP size limit exceeded")
    if digest(base_zip.read_bytes()) != expected_sha256:
        raise ValueError("Base ZIP SHA-256 mismatch")
    with zipfile.ZipFile(base_zip) as archive:
        infos = archive.infolist()
        if not 1 <= len(infos) <= MAX_FILES + 1:
            raise ValueError("Unexpected ZIP entry count")
        names: set[str] = set()
        folded: set[str] = set()
        total = 0
        for info in infos:
            name = checked_name(info.filename)
            if name in names or name.casefold() in folded or info.is_dir():
                raise ValueError("Duplicate or directory ZIP entry")
            names.add(name)
            folded.add(name.casefold())
            mode = info.external_attr >> 16
            file_type = stat.S_IFMT(mode)
            if file_type != stat.S_IFREG or not (mode & stat.S_IROTH) or info.flag_bits & 1:
                raise ValueError("Link, unreadable non-file, or encrypted ZIP entry")
            total += info.file_size
            if info.file_size > MAX_FILE_BYTES or total > MAX_TOTAL_BYTES:
                raise ValueError("ZIP size limit exceeded")
        if "package-manifest.json" not in names:
            raise ValueError("Missing package manifest")
        data = {name: archive.read(name) for name in names}
    manifest = json.loads(data.pop("package-manifest.json"))
    entries = manifest.get("files")
    if manifest.get("schema_version") != 1 or not isinstance(entries, list):
        raise ValueError("Unsupported package manifest")
    declared: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"path", "size", "sha256"}:
            raise ValueError("Invalid manifest entry")
        name = checked_name(entry["path"])
        if name in declared or name == "package-manifest.json":
            raise ValueError("Duplicate manifest entry")
        declared.add(name)
        content = data.get(name)
        if (content is None or type(entry["size"]) is not int or
                entry["size"] != len(content) or entry["sha256"] != digest(content)):
            raise ValueError("Manifest byte mismatch")
    if declared != set(data) or not REQUIRED_BASE.issubset(declared):
        raise ValueError("Manifest and ZIP inventories differ")
    if any(name.startswith("integrations/results/") for name in declared):
        raise ValueError("Base already contains results sources")
    if b"ssv_results_blueprint" in data["function_app.py"]:
        raise ValueError("Base already registers results")
    return data, manifest


def patch_provenance(source: str, expected_review: str) -> str:
    """Apply the three isolated changes reviewed in mower/build_provenance.py."""
    replacements = (
        ('result["entrypoint_sha256"] = hashlib.sha256(entrypoint.read_bytes()).hexdigest()',
         'entrypoint_content = entrypoint.read_bytes()\n'
         '        result["entrypoint_sha256"] = hashlib.sha256(entrypoint_content).hexdigest()'),
        ('        # Check only application source locations, never site-packages or their',
         '        if b"app.register_functions(ssv_results_blueprint)" in entrypoint_content and not {\n'
         '            "integrations/results/results_blueprint.py",\n'
         '            "integrations/results/ssv_results/__init__.py",\n'
         '            "integrations/results/ssv_results/__main__.py",\n'
         '            "integrations/results/ssv_results/collector.py",\n'
         '            "integrations/results/ssv_results/parsers.py",\n'
         '        }.issubset(names):\n'
         '            raise ValueError("INCOMPLETE_MANIFEST")\n'
         '        # Check only application source locations, never site-packages or their'),
        ('for folder in ("mower", "occupancy"):',
         'for folder in ("mower", "occupancy", "integrations/results"):'))
    changed = source
    for old, new in replacements:
        if changed.count(old) != 1:
            raise ValueError("Base provenance differs from reviewed patch location")
        changed = changed.replace(old, new)
    if ast.dump(ast.parse(changed)) != ast.dump(ast.parse(expected_review)):
        raise ValueError("Provenance patch differs from reviewed implementation")
    return changed


def _source_files(repo: Path) -> dict[str, bytes]:
    result = {}
    for name in RESULT_FILES:
        path = repo / name
        if not path.is_file() or path.is_symlink():
            raise ValueError("Missing or linked result source: " + name)
        result[name] = path.read_bytes()
    return result


def _probe(stage: Path) -> dict:
    script = """
import json, socket
from pathlib import Path
def denied(*args, **kwargs): raise AssertionError('Network forbidden during import proof')
socket.socket.connect = denied
socket.socket.connect_ex = denied
socket.socket.sendto = denied
socket.create_connection = denied
socket.getaddrinfo = denied
import function_app
from mower.build_provenance import inspect_installed_package
print(json.dumps({'functions': sorted(f.get_function_name() for f in function_app.app.get_functions()),
                  'provenance': inspect_installed_package(Path.cwd())}))
"""
    environment = {key: value for key, value in os.environ.items()
                   if key.upper() in {"SYSTEMROOT", "WINDIR", "TEMP", "TMP"}}
    environment.update(PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1")
    completed = subprocess.run([sys.executable, "-B", "-c", script], cwd=stage,
                               env=environment, capture_output=True, text=True, check=True)
    return json.loads(completed.stdout)


def write_release_zip(base_zip: Path, release: Path, base: dict[str, bytes],
                      patched: dict[str, bytes], manifest_bytes: bytes) -> None:
    """Retain each original entry's ZIP metadata; give new modules readable mode."""
    with zipfile.ZipFile(base_zip) as base_archive, \
            zipfile.ZipFile(release, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        reference = base_archive.getinfo("function_app.py")
        for name, content in sorted(patched.items()):
            if name in base:
                info = copy.copy(base_archive.getinfo(name))
            else:
                info = zipfile.ZipInfo(name, ZIP_TIMESTAMP)
                info.create_system = reference.create_system
                info.external_attr = reference.external_attr
                info.internal_attr = reference.internal_attr
                info.compress_type = reference.compress_type
            archive.writestr(info, content)
        archive.writestr(copy.copy(base_archive.getinfo("package-manifest.json")), manifest_bytes)


def validate_function_delta(before: list[str], after: list[str]) -> None:
    old_functions, new_functions = set(before), set(after)
    if (len(old_functions) != 16 or len(new_functions) != 18 or
            new_functions - old_functions != {"ssv53_results_read", "ssv53_results_update"} or
            not old_functions.issubset(new_functions)):
        raise ValueError("Unexpected FunctionApp registration delta")


def prepare(base_zip: Path, expected_sha256: str, output: Path, repo: Path) -> dict:
    base_zip, output, repo = base_zip.resolve(), output.resolve(), repo.resolve()
    if output.exists():
        raise ValueError("Output directory already exists")
    base, manifest = read_verified_base(base_zip, expected_sha256)
    dependencies = base["requirements.txt"].decode("utf-8").splitlines()
    for line in (HERE / "requirements.txt").read_text("utf-8").splitlines():
        if line.strip() and not line.startswith("#") and line not in dependencies:
            raise ValueError("Result dependency differs from base release: " + line)
    reviewed_provenance = (repo / "mower/build_provenance.py").read_text("utf-8")
    patched = dict(base)
    patched["function_app.py"] = patch_function(base["function_app.py"].decode("utf-8")).encode("utf-8")
    patched["mower/build_provenance.py"] = patch_provenance(
        base["mower/build_provenance.py"].decode("utf-8"), reviewed_provenance).encode("utf-8")
    patched.update(_source_files(repo))
    new_manifest = dict(manifest)
    new_manifest["files"] = [{"path": name, "size": len(content), "sha256": digest(content)}
                             for name, content in sorted(patched.items())]
    new_manifest["ssv_results_integration"] = {"source": "integrations/results",
                                                  "enabled_default": False,
                                                  "base_zip_sha256": expected_sha256}
    manifest_bytes = (json.dumps(new_manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
    output.mkdir(parents=True)
    stage = output / "stage"
    stage.mkdir()
    for name, content in patched.items():
        target = stage / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    (stage / "package-manifest.json").write_bytes(manifest_bytes)
    with tempfile.TemporaryDirectory(dir=output) as directory:
        baseline = Path(directory)
        for name, content in base.items():
            target = baseline / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        with zipfile.ZipFile(base_zip) as archive:
            (baseline / "package-manifest.json").write_bytes(archive.read("package-manifest.json"))
        before = _probe(baseline)
    after = _probe(stage)
    validate_function_delta(before["functions"], after["functions"])
    if not before["provenance"]["manifest_files_verified"] or not after["provenance"]["manifest_files_verified"]:
        raise ValueError("Package provenance verification failed")
    release = output / "results-release.zip"
    write_release_zip(base_zip, release, base, patched, manifest_bytes)
    unchanged = set(base) - {"function_app.py", "mower/build_provenance.py"}
    with zipfile.ZipFile(release) as archive:
        if any(archive.read(name) != base[name] for name in unchanged):
            raise ValueError("Existing release bytes changed")
        with zipfile.ZipFile(base_zip) as base_archive:
            for name in (*base, "package-manifest.json"):
                old_info, new_info = base_archive.getinfo(name), archive.getinfo(name)
                if (old_info.create_system, old_info.external_attr, old_info.internal_attr,
                        old_info.compress_type, old_info.date_time) != (
                        new_info.create_system, new_info.external_attr, new_info.internal_attr,
                        new_info.compress_type, new_info.date_time):
                    raise ValueError("Existing ZIP metadata changed: " + name)
        for name in RESULT_FILES:
            info = archive.getinfo(name)
            if stat.S_IFMT(info.external_attr >> 16) != stat.S_IFREG or not (
                    (info.external_attr >> 16) & stat.S_IROTH):
                raise ValueError("Result source lacks regular-file readable mode: " + name)
    with zipfile.ZipFile(base_zip) as archive:
        base_manifest_sha256 = digest(archive.read("package-manifest.json"))
    proof = {"base_zip_sha256": expected_sha256, "base_manifest_sha256": base_manifest_sha256,
             "release_sha256": digest(release.read_bytes()), "release_manifest_sha256": digest(manifest_bytes),
             "unchanged_existing_files": len(unchanged),
             "modified_existing_files": ["function_app.py", "mower/build_provenance.py"],
             "added_files": list(RESULT_FILES), "functions_before": before["functions"],
             "functions_after": after["functions"], "network_allowed_during_import": False,
             "deployed": False, "cms_published": False}
    (output / "proof.json").write_text(json.dumps(proof, ensure_ascii=False, indent=2) + "\n", "utf-8")
    return proof


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-zip", required=True, type=Path)
    parser.add_argument("--expected-base-sha256", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--repo", type=Path, default=HERE.parents[1])
    args = parser.parse_args()
    print(json.dumps(prepare(args.base_zip, args.expected_base_sha256, args.output, args.repo), indent=2))


if __name__ == "__main__":
    main()
