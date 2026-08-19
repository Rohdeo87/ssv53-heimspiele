from __future__ import annotations

import hashlib
import json
import stat
import zipfile
from pathlib import Path, PurePosixPath


REQUIRED_FILES = (
    "function_app.py",
    "host.json",
    "order_mail.py",
    "occupancy_notifications.py",
    "occupancy/__init__.py",
    "occupancy/config.json",
    "occupancy/match_model.py",
    "occupancy/service.py",
    "requirements.txt",
    "training_cancellations.py",
    "special_occupancy.py",
    "mower/__init__.py",
    "mower/config.json",
    "mower/config_source.py",
    "mower/controller.py",
    "mower/decision.py",
    "mower/dry_run.py",
    "mower/husqvarna.py",
    "mower/husqvarna_actions.py",
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

# PARK_ONLY darf genau den Park-Endpunkt enthalten. Start-/Resume-Funktionen
# bleiben im gesamten produktiven Paket technisch verboten.
FORBIDDEN_CONTENT_MARKERS = (
    b'"type": "Start"',
    b"ResumeSchedule",
    b"StartInWorkArea",
    b"def start_",
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
            if marker in content:
                raise ValueError(
                    f"Verbotene Start-/Geheimnislogik entdeckt: "
                    f"{relative_path} enthält "
                    f"{marker.decode('utf-8', errors='replace')!r}"
                )
        files.append((normalized, content))

    if missing:
        raise FileNotFoundError(
            "Erforderliche Dateien fehlen: " + ", ".join(sorted(missing))
        )

    # Der Husqvarna-Write-Endpunkt darf exakt in einem dedizierten Modul liegen.
    action_files = [
        name for name, content in files
        if b"/actions" in content
    ]
    if action_files != ["mower/husqvarna_actions.py"]:
        raise ValueError(
            "PARK_ONLY Schreiblogik ist nicht exakt auf "
            "mower/husqvarna_actions.py begrenzt: "
            + ", ".join(action_files)
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
        "safety_stage": "PARK_ONLY_CAPABLE_LOCKED",
        "remote_build_required": True,
        "automatic_start_implemented": False,
        "park_write_gate_required": True,
        "files": manifest_files,
    }
    manifest_bytes = (
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
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
            "mower/husqvarna_actions.py",
            "mower/park_only.py",
            "package-manifest.json",
        ):
            if required not in names:
                raise ValueError(f"PARK_ONLY Paketeintrag fehlt: {required}")
        bad = archive.testzip()
        if bad is not None:
            raise ValueError(f"Beschädigter ZIP-Eintrag: {bad}")

    package_bytes = output_path.read_bytes()
    return {
        "output": str(output_path),
        "size": len(package_bytes),
        "sha256": _sha256(package_bytes),
        "file_count": len(manifest_files) + 1,
        "safety_stage": "PARK_ONLY_CAPABLE_LOCKED",
        "remote_build_required": True,
    }


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--repository-root", default=".")
    parser.add_argument(
        "--output",
        default="dist/ssv53-platzpflege-park-only-source.zip",
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
