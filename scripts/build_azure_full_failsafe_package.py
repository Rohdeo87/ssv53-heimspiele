from __future__ import annotations

import argparse
import hashlib
import json
import stat
import zipfile
from pathlib import Path, PurePosixPath


REQUIRED_FILES = (
    "daily_safety_report.py",
    "function_app.py",
    "host.json",
    "order_mail.py",
    "occupancy_notifications.py",
    "platzwart_console.py",
    "occupancy/__init__.py",
    "occupancy/config.json",
    "occupancy/match_model.py",
    "occupancy/runtime_source.py",
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
    "mower/full_failsafe.py",
    "mower/full_mower.py",
    "mower/husqvarna.py",
    "mower/husqvarna_actions.py",
    "mower/husqvarna_cutting_height_actions.py",
    "mower/husqvarna_start_actions.py",
    "mower/husqvarna_statistics_actions.py",
    "mower/cutting_height.py",
    "mower/hydrawise.py",
    "mower/irrigation_recovery.py",
    "mower/irrigation_journal.py",
    "mower/hydrawise_actions.py",
    "mower/irrigation_schedule.py",
    "mower/park_only.py",
    "mower/planner.py",
    "mower/runtime.py",
    "mower/safety.py",
    "mower/state.py",
    "mower/state_store.py",
    "mower/weather.py",
    "mower/weather_store.py",
    "mower/weather_service.py",
    "mower/adaptive_planner.py",
    "public/rasen.ics",
    "public/kunstrasen.ics",
    "public/matches.json",
)

FORBIDDEN_PATH_PARTS = {".git", ".github", ".venv", "__pycache__", "generated", "infra", "tests"}
FORBIDDEN_CONTENT_MARKERS = (
    b"ResumeSchedule",
    b'"type": "Start"',
    b"manualrun.php",
    b"stopzone.php",
    b"SSV53_AUTOMATION_TOKEN",
)
FIXED_ZIP_TIMESTAMP = (2026, 1, 1, 0, 0, 0)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _safe_path(relative_path: str) -> PurePosixPath:
    path = PurePosixPath(relative_path)
    if path.is_absolute() or ".." in path.parts or FORBIDDEN_PATH_PARTS.intersection(path.parts):
        raise ValueError(f"Unsicherer Paketpfad: {relative_path}")
    return path


def collect_files(repository_root: Path) -> list[tuple[str, bytes]]:
    files: list[tuple[str, bytes]] = []
    for relative_path in REQUIRED_FILES:
        source = repository_root / relative_path
        if not source.is_file() or source.is_symlink():
            raise FileNotFoundError(f"Erforderliche Paketdatei fehlt: {relative_path}")
        content = source.read_bytes()
        for marker in FORBIDDEN_CONTENT_MARKERS:
            if marker.lower() in content.lower():
                raise ValueError(
                    f"Verbotene Befehlslogik entdeckt: {relative_path} enthält "
                    f"{marker.decode('utf-8', errors='replace')!r}"
                )
        files.append((_safe_path(relative_path).as_posix(), content))

    mower_action_files = sorted(name for name, content in files if b"/actions" in content)
    if mower_action_files != [
        "mower/husqvarna_actions.py",
        "mower/husqvarna_start_actions.py",
    ]:
        raise ValueError("Husqvarna-Schreiblogik liegt nicht ausschließlich in den geprüften Modulen.")
    statistics_reset_files = sorted(
        name
        for name, content in files
        if b"/statistics/resetCuttingBladeUsageTime" in content
    )
    if statistics_reset_files != ["mower/husqvarna_statistics_actions.py"]:
        raise ValueError(
            "Husqvarna-Statistik-Rücksetzung liegt nicht ausschließlich im geprüften Modul."
        )
    height_write_files = sorted(
        name
        for name, content in files
        if b"/workAreas/" in content and b'method="PATCH"' in content
    )
    if height_write_files != ["mower/husqvarna_cutting_height_actions.py"]:
        raise ValueError(
            "Schnitthöhen-Schreiblogik liegt nicht ausschließlich im geprüften Modul."
        )
    hydrawise_action_files = sorted(name for name, content in files if b"setzone.php" in content)
    if hydrawise_action_files != ["mower/hydrawise_actions.py"]:
        raise ValueError("Hydrawise-Schreiblogik liegt nicht ausschließlich im geprüften Aktionsmodul.")
    return sorted(files)


def build_package(repository_root: Path, output_path: Path) -> dict[str, object]:
    files = collect_files(repository_root)
    manifest = {
        "schema_version": 1,
        "package_type": "azure-functions-python-source",
        "safety_stage": "FULL_FAILSAFE_7_ZONES_150_MIN_ADAPTIVE_SHADOW_LOCKED",
        "remote_build_required": True,
        "automatic_park_implemented": True,
        "automatic_continuous_mowing_implemented": True,
        "automatic_irrigation_start_implemented": True,
        "work_area_cutting_height_control_implemented": True,
        "work_area_cutting_height_mm_range": [20, 60],
        "manual_failed_irrigation_reset_implemented": True,
        "manual_reset_requires_function_auth": True,
        "manual_reset_sends_device_commands": False,
        "training_cancellations_implemented": True,
        "training_cancellations_fail_closed": True,
        "training_cancellation_release_delay_minutes": 30,
        "dynamic_special_occupancy_implemented": True,
        "dynamic_special_occupancy_fail_closed": True,
        "central_collision_notifications_implemented": True,
        "collision_notification_recipients_server_configured": True,
        "trainer_contact_registration_removed": True,
        "notification_timer_separate_from_mower": True,
        "expected_hydrawise_zone_count": 7,
        "expected_hydrawise_relay_ids": [
            9104894,
            9104906,
            9104909,
            9104911,
            9104913,
            9104920,
            9104921,
        ],
        "hydrawise_schedule_suspension_required": True,
        "hydrawise_zone_start_confirmation_required": True,
        "hydrawise_zone_end_confirmation_required": True,
        "post_irrigation_drying_minutes": 150,
        "mower_active_park_lead_minutes": 4,
        "adaptive_planning_shadow_only": True,
        "adaptive_execution_locked": True,
        "adaptive_forecast_archive_days": 21,
        "adaptive_daily_shadow_report": True,
        "adaptive_forecast_archive_can_send_device_commands": False,
        "weather_provider_default": "OPEN_METEO",
        "weather_paid_calls_supported": False,
        "weather_monthly_call_hard_limit": 900,
        "irrigation_plan_lease_minutes": 3,
        "irrigation_suspension_revalidation_cycles": 2,
        "irrigation_suspension_revalidation_max_gap_seconds": 90,
        "irrigation_plan_change_confirmation_minutes": 2,
        "hydrawise_app_suspension_releases_unused_window": True,
        "hydrawise_future_zone_duration_changes_supported": True,
        "hydrawise_active_zone_command_remains_immutable": True,
        "hydrawise_confirmed_early_stop_cancels_remaining_zones": True,
        "partial_irrigation_safe_completion_implemented": True,
        "partial_irrigation_keeps_full_drying_hold": True,
        "partial_irrigation_requires_complete_fresh_relay_proof": True,
        "fresh_park_event_required_before_every_zone_start": True,
        "started_zone_tracking_uses_current_live_mower_snapshot": True,
        "irrigation_schedule_dashboard_controls_implemented": True,
        "irrigation_schedule_skip_next_implemented": True,
        "irrigation_schedule_pause_until_implemented": True,
        "irrigation_schedule_safe_resume_implemented": True,
        "irrigation_schedule_custom_next_run_implemented": True,
        "irrigation_schedule_per_zone_selection_implemented": True,
        "irrigation_schedule_per_zone_duration_implemented": True,
        "irrigation_schedule_two_cycle_confirmation_required": True,
        "irrigation_schedule_mower_park_precedence_required": True,
        "park_write_gate_required": True,
        "start_write_gate_required": True,
        "irrigation_write_gate_required": True,
        "exact_confirmation_required": True,
        "files": [
            {"path": name, "size": len(content), "sha256": _sha256(content)}
            for name, content in files
        ],
    }
    manifest_bytes = (json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, content in files + [("package-manifest.json", manifest_bytes)]:
            info = zipfile.ZipInfo(name, FIXED_ZIP_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            archive.writestr(info, content)
    return {
        "output": str(output_path),
        "sha256": _sha256(output_path.read_bytes()),
        "file_count": len(files) + 1,
        "manifest": manifest,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, default=Path("dist/ssv53-platzpflege-full-failsafe-source.zip"))
    arguments = parser.parse_args()
    result = build_package(arguments.repository_root.resolve(), arguments.output.resolve())
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
