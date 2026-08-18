from __future__ import annotations

import argparse
import hashlib
import json
import stat
import zipfile
from pathlib import Path, PurePosixPath


REQUIRED_FILES = (
    "function_app.py",
    "host.json",
    "order_mail.py",
    "occupancy/__init__.py",
    "occupancy/config.json",
    "occupancy/match_model.py",
    "occupancy/service.py",
    "requirements.txt",
    "mower/__init__.py",
    "mower/config.json",
    "mower/config_source.py",
    "mower/controller.py",
    "mower/decision.py",
    "mower/dry_run.py",
    "mower/full_mower.py",
    "mower/husqvarna.py",
    "mower/husqvarna_actions.py",
    "mower/husqvarna_start_actions.py",
    "mower/hydrawise.py",
    "mower/irrigation_recovery.py",
    "mower/park_only.py",
    "mower/planner.py",
    "mower/runtime.py",
    "mower/safety.py",
    "mower/state.py",
    "mower/state_store.py",
    "public/rasen.ics",
    "public/kunstrasen.ics",
    "public/matches.json",
)

FORBIDDEN_PATH_PARTS = {
    ".git",
    ".github",
    ".venv",
    "__pycache__",
    "generated",
    "infra",
    "tests",
}

# Dieses Paket darf den Mäher parken und zeitlich begrenzt in genau einem
# Arbeitsbereich starten. Hydrawise bleibt ausnahmslos read-only.
FORBIDDEN_CONTENT_MARKERS = (
    b'ResumeSchedule',
    b'"type": "Start"',
    b"manualrun.php",
    b"stopzone.php",
    b"setzone.php",
    b"SSV53_AUTOMATION_TOKEN",
)

FIXED_ZIP_TIMESTAMP = (2026, 1, 1, 0, 0, 0)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _validate_relative_path(relative_path: str) -> PurePosixPath:
    path = PurePosixPath(relative_path)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"Unsicherer Paketpfad: {relative_path}")
    if FORBIDDEN_PATH_PARTS.intersection(path.parts):
        raise ValueError(f"Verbotener Paketpfad: {relative_path}")
    return path


def collect_files(repository_root: Path) -> list[tuple[str, bytes]]:
    files: list[tuple[str, bytes]] = []
    missing: list[str] = []
    for relative_path in REQUIRED_FILES:
        source = repository_root / relative_path
        if not source.is_file():
            missing.append(relative_path)
            continue
        if source.is_symlink():
            raise ValueError(f"Symlinks werden nicht paketiert: {relative_path}")
        normalized = _validate_relative_path(relative_path).as_posix()
        content = source.read_bytes()
        for marker in FORBIDDEN_CONTENT_MARKERS:
            if marker.lower() in content.lower():
                raise ValueError(
                    f"Verbotene Befehlslogik entdeckt: {relative_path} "
                    f"enthält {marker.decode('utf-8', errors='replace')!r}"
                )
        files.append((normalized, content))

    if missing:
        raise FileNotFoundError(
            "Erforderliche Dateien fehlen: " + ", ".join(sorted(missing))
        )

    action_files = sorted(
        name for name, content in files if b"/actions" in content
    )
    expected_action_files = [
        "mower/husqvarna_actions.py",
        "mower/husqvarna_start_actions.py",
    ]
    if action_files != expected_action_files:
        raise ValueError(
            "Mäher-Schreiblogik ist nicht auf die zwei geprüften Module "
            "begrenzt: " + ", ".join(action_files)
        )
    return sorted(files, key=lambda item: item[0])


def build_package(repository_root: Path, output_path: Path) -> dict[str, object]:
    files = collect_files(repository_root)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_files = [
        {"path": name, "size": len(content), "sha256": _sha256(content)}
        for name, content in files
    ]
    manifest = {
        "schema_version": 1,
        "package_type": "azure-functions-python-source",
        "safety_stage": "FULL_MOWER_CAPABLE_LOCKED",
        "remote_build_required": True,
        "automatic_start_implemented": True,
        "automatic_restart_sources": ["match", "training"],
        "hydrawise_continuous_clear_confirmation_required": True,
        "hydrawise_write_functions_present": False,
        "park_write_gate_required": True,
        "start_write_gate_required": True,
        "exact_confirmation_required": True,
        "files": manifest_files,
    }
    manifest_bytes = (
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n"
    ).encode("utf-8")

    with zipfile.ZipFile(
        output_path,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as archive:
        for name, content in files + [("package-manifest.json", manifest_bytes)]:
            info = zipfile.ZipInfo(name, date_time=FIXED_ZIP_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            archive.writestr(info, content)

    with zipfile.ZipFile(output_path, mode="r") as archive:
        names = set(archive.namelist())
        for required in (
            "host.json",
            "function_app.py",
            "mower/full_mower.py",
            "mower/husqvarna_actions.py",
            "mower/husqvarna_start_actions.py",
            "package-manifest.json",
        ):
            if required not in names:
                raise ValueError(f"FULL_MOWER Paketeintrag fehlt: {required}")
        bad = archive.testzip()
        if bad is not None:
            raise ValueError(f"Beschädigter ZIP-Eintrag: {bad}")

    package_bytes = output_path.read_bytes()
    return {
        "output": str(output_path),
        "size": len(package_bytes),
        "sha256": _sha256(package_bytes),
        "file_count": len(manifest_files) + 1,
        "safety_stage": "FULL_MOWER_CAPABLE_LOCKED",
        "remote_build_required": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository-root", default=".")
    parser.add_argument(
        "--output",
        default="dist/ssv53-platzpflege-full-mower-locked-source.zip",
    )
    args = parser.parse_args()
    result = build_package(
        Path(args.repository_root).resolve(),
        Path(args.output).resolve(),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
