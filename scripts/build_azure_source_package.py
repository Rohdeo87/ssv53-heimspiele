from __future__ import annotations

# Prüf-Trigger nach Synchronisierung mit main; keine Funktionsänderung.

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
    "training_cancellations.py",
    "special_occupancy.py",
    "mower/__init__.py",
    "mower/config.json",
    "mower/config_source.py",
    "mower/controller.py",
    "mower/decision.py",
    "mower/dry_run.py",
    "mower/husqvarna.py",
    "mower/hydrawise.py",
    "mower/irrigation_recovery.py",
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

FORBIDDEN_CONTENT_MARKERS = (
    b"/actions",
    b"ParkUntilFurtherNotice",
    b"StartInWorkArea",
    b"send_mower_action",
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
                    f"Schreib- oder Geheimnislogik im Azure-Quellpaket entdeckt: "
                    f"{relative_path} enthält {marker.decode('utf-8', errors='replace')!r}"
                )

        files.append((normalized, content))

    if missing:
        raise FileNotFoundError(
            "Erforderliche Dateien fehlen: " + ", ".join(sorted(missing))
        )

    names = [name for name, _content in files]
    if len(names) != len(set(names)):
        raise ValueError("Doppelte Paketpfade erkannt.")

    return sorted(files, key=lambda item: item[0])


def build_package(repository_root: Path, output_path: Path) -> dict[str, object]:
    files = collect_files(repository_root)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    manifest_files = [
        {
            "path": name,
            "size": len(content),
            "sha256": _sha256(content),
        }
        for name, content in files
    ]
    manifest = {
        "schema_version": 1,
        "package_type": "azure-functions-python-source",
        "safety_stage": "DRY_RUN_READ_ONLY",
        "device_interfaces_read_only": True,
        "persistent_safety_state_write": True,
        "remote_build_required": True,
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
        names = archive.namelist()
        if "host.json" not in names:
            raise ValueError("host.json liegt nicht im Wurzelverzeichnis des Pakets.")
        if "function_app.py" not in names:
            raise ValueError(
                "function_app.py liegt nicht im Wurzelverzeichnis des Pakets."
            )
        if any(name.startswith("/") or ".." in PurePosixPath(name).parts for name in names):
            raise ValueError("Das erzeugte ZIP enthält einen unsicheren Pfad.")
        bad = archive.testzip()
        if bad is not None:
            raise ValueError(f"Beschädigter ZIP-Eintrag: {bad}")

    package_bytes = output_path.read_bytes()
    result = {
        "output": str(output_path),
        "size": len(package_bytes),
        "sha256": _sha256(package_bytes),
        "file_count": len(manifest_files) + 1,
        "safety_stage": "DRY_RUN_READ_ONLY",
        "remote_build_required": True,
    }
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Erzeugt ein geprüftes Azure-Functions-Quellpaket. "
            "Es wird nichts nach Azure übertragen."
        )
    )
    parser.add_argument(
        "--repository-root",
        default=".",
        help="Wurzelverzeichnis des Repositorys",
    )
    parser.add_argument(
        "--output",
        default="dist/ssv53-platzpflege-source.zip",
        help="Zielpfad des ZIP-Pakets",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = build_package(
        Path(args.repository_root).resolve(),
        Path(args.output).resolve(),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
