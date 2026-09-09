from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import zipfile
from datetime import datetime, timezone
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "scripts" / "check_release_readiness.py"
SPEC = importlib.util.spec_from_file_location("check_release_readiness", MODULE_PATH)
assert SPEC and SPEC.loader
release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release)
NOW = datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc)


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def write_json(root: Path, name: str, payload: object) -> dict:
    path = root / "evidence" / f"{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    path.write_bytes(raw)
    return {"path": path.relative_to(root).as_posix(), "sha256": digest(raw)}


def receipt(root: Path, name: str, head: str, item: str, kind: str, *, status: str = "PASS",
            valid_from: str = "2026-09-09T11:00:00Z", valid_until: str = "2026-09-09T13:00:00Z",
            subject_extra: dict[str, str] | None = None) -> dict:
    subject = {"head": head, "binding_item": item}
    subject.update(subject_extra or {})
    return write_json(root, name, {
        "schema_version": 1, "evidence_kind": kind, "status": status,
        "subject": subject,
        "valid_from_utc": valid_from, "valid_until_utc": valid_until,
        "provenance": {"evidence_id": f"receipt-{name}", "source": "local-audit", "authority": "release-authority", "observer": "named-operator", "observed_at_utc": "2026-09-09T11:30:00Z"},
        "findings": ["bounded inspection passed"],
    })


def git_source(root: Path) -> tuple[str, dict[str, bytes]]:
    files = {"function_app.py": b"print('safe')\n", "mower/controller.py": b"MODE = 'FULL_FAILSAFE'\n"}
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    for command in (("init",), ("config", "user.email", "test@example.invalid"), ("config", "user.name", "Release Test"), ("add", "."), ("commit", "-m", "source")):
        subprocess.run(["git", "-C", str(root), *command], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    head = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], check=True, stdout=subprocess.PIPE, text=True).stdout.strip()
    return head, files


def package(root: Path, files: dict[str, bytes]) -> tuple[dict, dict]:
    entries = [{"path": name, "sha256": digest(content), "size": len(content)} for name, content in sorted(files.items())]
    manifest = json.dumps({"schema_version": 1, "files": entries}, sort_keys=True, separators=(",", ":")).encode("utf-8")
    path = root / "dist" / "package.zip"
    path.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
        archive.writestr("package-manifest.json", manifest)
    return {"path": path.relative_to(root).as_posix(), "sha256": digest(path.read_bytes())}, {"sha256": digest(manifest)}


def ready_bundle(root: Path, mode: str = "LIMITED_SUPERVISED_FIRST_ACTIVATION") -> dict:
    head, files = git_source(root)
    artifact, manifest = package(root, files)
    binding = {
        "head": head,
        "package": {**artifact, "source_head": head, "receipt": receipt(root, "package", head, "package", "package_receipt")},
        "manifest": {**manifest, "receipt": receipt(root, "manifest", head, "manifest", "manifest_receipt")},
        "calendar": receipt(root, "calendar", head, "calendar", "calendar_approval"),
        "need": receipt(root, "need", head, "need", "watering_need_approval"),
        "window": {"evidence": receipt(root, "window", head, "window", "window_approval", subject_extra={"window_id": "supervised-run-001", "starts_at_utc": "2026-09-09T12:15:00Z", "ends_at_utc": "2026-09-09T13:15:00Z"}), "detail": {"window_id": "supervised-run-001", "starts_at_utc": "2026-09-09T12:15:00Z", "ends_at_utc": "2026-09-09T13:15:00Z"}},
    }
    bundle = {
        "schema_version": 2, "stage": "PRE_ACTIVATION", "release_mode": mode, "approved_binding": binding,
        "exact_head_ci": {"head": head, "conclusion": "success", "evidence": receipt(root, "ci", head, "exact_head_ci", "ci_receipt")},
        "immutable_assumptions": {"training_calendar": binding["calendar"], "approved_watering": binding["need"]},
        "pre_activation_safety_evidence": {name: receipt(root, name, head, name, f"{name}_evidence") for name in release.PRE_ACTIVATION_GATES},
        "post_activation_evidence": {},
        "final_operator_approval": {"state": "PENDING", "one_shot": True, "approval_request_id": "operator-request-001", "binding_sha256": release._canonical_hash(binding)},
    }
    return bundle


def rebind(bundle: dict) -> None:
    bundle["final_operator_approval"]["binding_sha256"] = release._canonical_hash(bundle["approved_binding"])


def test_supervised_preflight_requires_real_pass_receipts_and_never_approves(tmp_path: Path):
    report = release.validate(ready_bundle(tmp_path), tmp_path, now=NOW)
    assert report["decision"] == "READY_FOR_FINAL_APPROVAL"
    assert report["authorization"].startswith("NONE:")
    assert report["operator_approval"] == "PENDING_ONLY"
    assert report["cannot_be_physically_verified_before_activation"] == list(release.POST_ACTIVATION_GATES)


def test_rejects_empty_or_negative_receipt_even_with_matching_file_hash(tmp_path: Path):
    bundle = ready_bundle(tmp_path)
    wrapper = bundle["pre_activation_safety_evidence"]["station"]
    write_json(tmp_path, "station", {})
    wrapper["sha256"] = digest((tmp_path / wrapper["path"]).read_bytes())
    report = release.validate(bundle, tmp_path, now=NOW)
    assert report["decision"] == "NOT_READY"
    assert any("receipt schema" in error for error in report["errors"])
    head = bundle["approved_binding"]["head"]
    wrapper.update(receipt(tmp_path, "station", head, "station", "station_evidence", status="FAIL"))
    report = release.validate(bundle, tmp_path, now=NOW)
    assert any("matching PASS" in error for error in report["errors"])


def test_rejects_stale_and_future_receipts(tmp_path: Path):
    bundle = ready_bundle(tmp_path)
    head = bundle["approved_binding"]["head"]
    bundle["pre_activation_safety_evidence"]["field"] = receipt(tmp_path, "field", head, "field", "field_evidence", valid_until="2026-09-09T11:59:00Z")
    report = release.validate(bundle, tmp_path, now=NOW)
    assert any("stale, future" in error for error in report["errors"])
    bundle["pre_activation_safety_evidence"]["field"] = receipt(tmp_path, "field", head, "field", "field_evidence", valid_from="2026-09-09T12:01:00Z", valid_until="2026-09-09T13:00:00Z")
    report = release.validate(bundle, tmp_path, now=NOW)
    assert any("stale, future" in error for error in report["errors"])


def test_rejects_expired_or_receipt_unbound_approval_window(tmp_path: Path):
    bundle = ready_bundle(tmp_path)
    detail = bundle["approved_binding"]["window"]["detail"]
    detail["starts_at_utc"], detail["ends_at_utc"] = "2026-09-09T10:00:00Z", "2026-09-09T11:00:00Z"
    rebind(bundle)
    report = release.validate(bundle, tmp_path, now=NOW)
    assert any("window has already expired" in error for error in report["errors"])
    detail["starts_at_utc"], detail["ends_at_utc"] = "2026-09-09T12:30:00Z", "2026-09-09T13:30:00Z"
    rebind(bundle)
    report = release.validate(bundle, tmp_path, now=NOW)
    assert any("receipt subject is not bound" in error for error in report["errors"])


def test_rejects_wrong_package_source_head_and_corrupted_zip(tmp_path: Path):
    bundle = ready_bundle(tmp_path)
    bundle["approved_binding"]["package"]["source_head"] = "0" * 40
    rebind(bundle)
    report = release.validate(bundle, tmp_path, now=NOW)
    assert any("ZIP checksum or source head" in error for error in report["errors"])
    bundle = ready_bundle(tmp_path / "corrupted")
    zip_path = tmp_path / "corrupted" / bundle["approved_binding"]["package"]["path"]
    zip_path.write_bytes(b"not a ZIP")
    report = release.validate(bundle, tmp_path / "corrupted", now=NOW)
    assert any("ZIP checksum or source head" in error for error in report["errors"])


def test_rejects_zip_with_self_consistent_manifest_but_wrong_git_bytes(tmp_path: Path):
    bundle = ready_bundle(tmp_path)
    files = {
        "function_app.py": b"print('tampered')\n",
        "mower/controller.py": (tmp_path / "mower" / "controller.py").read_bytes(),
    }
    artifact, manifest = package(tmp_path, files)
    bundle["approved_binding"]["package"].update(artifact)
    bundle["approved_binding"]["manifest"]["sha256"] = manifest["sha256"]
    rebind(bundle)
    report = release.validate(bundle, tmp_path, now=NOW)
    assert any("declared Git source bytes failed" in error for error in report["errors"])


def test_rejects_different_ci_head_and_missing_native_schedule(tmp_path: Path):
    bundle = ready_bundle(tmp_path)
    bundle["exact_head_ci"]["head"] = "b" * 40
    del bundle["pre_activation_safety_evidence"]["native_schedule"]
    report = release.validate(bundle, tmp_path, now=NOW)
    assert report["decision"] == "NOT_READY"
    assert any("exact_head_ci" in error for error in report["errors"])
    assert any("native schedule" in error for error in report["errors"])


def test_full_unattended_rollout_requires_semantic_post_activation_evidence(tmp_path: Path):
    bundle = ready_bundle(tmp_path, "FULL_UNATTENDED_ROLLOUT")
    report = release.validate(bundle, tmp_path, now=NOW)
    assert sum("full unattended rollout" in error for error in report["errors"]) == 3
    head = bundle["approved_binding"]["head"]
    bundle["post_activation_evidence"] = {name: receipt(tmp_path, f"post-{name}", head, name, f"{name}_evidence") for name in release.POST_ACTIVATION_GATES}
    assert release.validate(bundle, tmp_path, now=NOW)["decision"] == "READY_FOR_FINAL_APPROVAL"


def test_rejects_approved_state_and_binding_tamper(tmp_path: Path):
    bundle = ready_bundle(tmp_path)
    bundle["final_operator_approval"].update({"state": "APPROVED", "binding_sha256": "0" * 64})
    report = release.validate(bundle, tmp_path, now=NOW)
    assert report["decision"] == "NOT_READY"
    assert any("must remain PENDING" in error for error in report["errors"])
