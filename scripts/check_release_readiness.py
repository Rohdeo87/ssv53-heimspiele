"""Validate offline release evidence without approving or activating a release.

Receipts are local, byte-bound statements from named sources.  This checker
validates their structure, freshness and relationship to local source bytes,
but does not authenticate an authority or replace the required external human
review of the underlying field, device and operating evidence.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

MAX_INPUT_BYTES = 256 * 1024
MAX_JSON_BYTES = 128 * 1024
MAX_ZIP_BYTES = 10 * 1024 * 1024
MAX_ZIP_FILES = 250
MAX_UNCOMPRESSED_BYTES = 5 * 1024 * 1024
SHA256 = re.compile(r"[0-9a-f]{64}")
GIT_SHA = re.compile(r"[0-9a-f]{40}")
SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{2,127}")
MODES = {"LIMITED_SUPERVISED_FIRST_ACTIVATION", "FULL_UNATTENDED_ROLLOUT"}
PRE_ACTIVATION_GATES = ("field", "station", "native_schedule", "controller_ownership")
POST_ACTIVATION_GATES = ("installed_bytes", "shadow_observation", "supervised_pilot")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_hash(value: Any) -> str:
    return _sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8"))


def _time(value: Any) -> datetime | None:
    if not isinstance(value, str) or len(value) > 64:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo and parsed.utcoffset() is not None else None


def _bounded(value: Any, depth: int = 0) -> bool:
    if depth > 12:
        return False
    if isinstance(value, dict):
        return len(value) <= 64 and all(isinstance(key, str) and len(key) <= 128 and _bounded(item, depth + 1)
                                        for key, item in value.items())
    if isinstance(value, list):
        return len(value) <= 32 and all(_bounded(item, depth + 1) for item in value)
    return not isinstance(value, str) or len(value) <= 4096


def _inside(repository: Path, value: Any, suffix: str | None = None) -> Path | None:
    if not isinstance(value, str) or not value or len(value) > 512:
        return None
    candidate = Path(value)
    if candidate.is_absolute() or ".." in candidate.parts or (suffix and candidate.suffix.lower() != suffix):
        return None
    resolved = (repository / candidate).resolve()
    try:
        resolved.relative_to(repository)
    except ValueError:
        return None
    return resolved


def _safe_zip_name(name: str) -> bool:
    path = PurePosixPath(name)
    return bool(name) and "\\" not in name and not path.is_absolute() and ".." not in path.parts and path.as_posix() == name


def _error(errors: list[str], label: str, message: str) -> None:
    errors.append(f"{label}: {message}")


def _evidence(record: Any, repository: Path, label: str, kind: str, head: str, now: datetime,
              errors: list[str], *, subject_item: str | None = None,
              subject_equals: dict[str, str] | None = None) -> dict[str, Any] | None:
    """Load a bounded, current PASS receipt with named authority and subject."""
    if not isinstance(record, dict) or set(record) != {"path", "sha256"}:
        _error(errors, label, "requires a path and checksum")
        return None
    path = _inside(repository, record.get("path"), ".json")
    checksum = record.get("sha256")
    if path is None or not isinstance(checksum, str) or not SHA256.fullmatch(checksum):
        _error(errors, label, "receipt path or checksum is invalid")
        return None
    try:
        raw = path.read_bytes()
    except OSError:
        _error(errors, label, "receipt unavailable")
        return None
    if len(raw) > MAX_JSON_BYTES or _sha256(raw) != checksum:
        _error(errors, label, "receipt checksum mismatch or size bound exceeded")
        return None
    try:
        content = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        _error(errors, label, "receipt is not valid JSON")
        return None
    required = {"schema_version", "evidence_kind", "status", "subject", "valid_from_utc", "valid_until_utc", "provenance", "findings"}
    if not isinstance(content, dict) or not _bounded(content) or set(content) != required:
        _error(errors, label, "receipt schema is incomplete")
        return None
    if content["schema_version"] != 1 or content["evidence_kind"] != kind or content["status"] != "PASS":
        _error(errors, label, "receipt is not a matching PASS result")
        return None
    subject = content["subject"]
    expected_subject = {"head": head, "binding_item": subject_item or label.split(".")[-1]}
    if subject_equals:
        expected_subject.update(subject_equals)
    if not isinstance(subject, dict) or subject != expected_subject:
        _error(errors, label, "receipt subject is not bound to this head and item")
        return None
    valid_from, valid_until = _time(content["valid_from_utc"]), _time(content["valid_until_utc"])
    provenance = content["provenance"]
    observed = _time(provenance.get("observed_at_utc")) if isinstance(provenance, dict) else None
    if not valid_from or not valid_until or valid_until <= valid_from or not (valid_from <= now <= valid_until):
        _error(errors, label, "receipt is stale, future, or has an invalid validity interval")
        return None
    if (not isinstance(provenance, dict) or set(provenance) != {"evidence_id", "source", "authority", "observer", "observed_at_utc"}
            or not observed or not (valid_from <= observed <= now)
            or any(not isinstance(provenance[key], str) or not SAFE_ID.fullmatch(provenance[key])
                   for key in ("evidence_id", "source", "authority", "observer"))):
        _error(errors, label, "identifiable current authority provenance is missing")
        return None
    findings = content["findings"]
    if not isinstance(findings, list) or not findings or any(not isinstance(item, str) or not item.strip() for item in findings):
        _error(errors, label, "receipt findings are empty or malformed")
        return None
    return content


def _verify_zip(package: Any, manifest: Any, head: str, repository: Path, errors: list[str]) -> bool:
    if not isinstance(package, dict) or set(package) != {"path", "sha256", "source_head", "receipt"}:
        _error(errors, "approved_binding.package", "requires ZIP path, checksum, source head and receipt")
        return False
    if not isinstance(manifest, dict) or set(manifest) != {"sha256", "receipt"}:
        _error(errors, "approved_binding.manifest", "requires manifest checksum and receipt")
        return False
    artifact = _inside(repository, package.get("path"), ".zip")
    if artifact is None or not artifact.is_file() or artifact.stat().st_size > MAX_ZIP_BYTES:
        _error(errors, "approved_binding.package", "ZIP unavailable or outside size bound")
        return False
    if package.get("source_head") != head or not isinstance(package.get("sha256"), str) or _sha256(artifact.read_bytes()) != package["sha256"]:
        _error(errors, "approved_binding.package", "ZIP checksum or source head does not match binding")
        return False
    if not isinstance(manifest.get("sha256"), str) or not SHA256.fullmatch(manifest["sha256"]):
        _error(errors, "approved_binding.manifest", "manifest checksum is invalid")
        return False
    try:
        with zipfile.ZipFile(artifact) as archive:
            infos = archive.infolist()
            if not 1 <= len(infos) <= MAX_ZIP_FILES or sum(info.file_size for info in infos) > MAX_UNCOMPRESSED_BYTES:
                raise ValueError
            names = [info.filename for info in infos]
            if len(set(names)) != len(names) or any(not _safe_zip_name(name) or info.is_dir() or (info.external_attr >> 16) & 0o170000 == 0o120000 for name, info in zip(names, infos)):
                raise ValueError
            if "package-manifest.json" not in names:
                raise ValueError
            raw_manifest = archive.read("package-manifest.json")
            if _sha256(raw_manifest) != manifest["sha256"]:
                raise ValueError
            parsed = json.loads(raw_manifest)
            entries = parsed.get("files") if isinstance(parsed, dict) and parsed.get("schema_version") == 1 else None
            if not isinstance(entries, list) or not 1 <= len(entries) < MAX_ZIP_FILES:
                raise ValueError
            expected: dict[str, dict[str, Any]] = {}
            for entry in entries:
                if not isinstance(entry, dict) or set(entry) != {"path", "sha256", "size"}:
                    raise ValueError
                name = entry["path"]
                if not isinstance(name, str) or not _safe_zip_name(name) or name in expected or not isinstance(entry["sha256"], str) or not SHA256.fullmatch(entry["sha256"]) or type(entry["size"]) is not int or entry["size"] < 0:
                    raise ValueError
                expected[name] = entry
            if set(names) != set(expected) | {"package-manifest.json"}:
                raise ValueError
            for name, entry in expected.items():
                content = archive.read(name)
                source = subprocess.run(["git", "-C", str(repository), "show", f"{head}:{name}"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False)
                if len(content) != entry["size"] or _sha256(content) != entry["sha256"] or source.returncode != 0 or source.stdout != content:
                    raise ValueError
    except (OSError, ValueError, zipfile.BadZipFile, json.JSONDecodeError):
        _error(errors, "approved_binding.package", "ZIP, manifest inventory, or declared Git source bytes failed verification")
        return False
    return True


def _binding(bundle: dict[str, Any], repository: Path, now: datetime, errors: list[str]) -> tuple[dict[str, Any] | None, str | None]:
    binding = bundle.get("approved_binding")
    required = {"head", "package", "manifest", "calendar", "need", "window"}
    if not isinstance(binding, dict) or set(binding) != required or not isinstance(binding.get("head"), str) or not GIT_SHA.fullmatch(binding["head"]):
        _error(errors, "approved_binding", "exact head, ZIP, manifest, calendar, need and window are required")
        return None, None
    head = binding["head"]
    _verify_zip(binding["package"], binding["manifest"], head, repository, errors)
    _evidence(binding.get("package", {}).get("receipt") if isinstance(binding.get("package"), dict) else None, repository, "approved_binding.package", "package_receipt", head, now, errors)
    _evidence(binding.get("manifest", {}).get("receipt") if isinstance(binding.get("manifest"), dict) else None, repository, "approved_binding.manifest", "manifest_receipt", head, now, errors)
    _evidence(binding["calendar"], repository, "approved_binding.calendar", "calendar_approval", head, now, errors)
    _evidence(binding["need"], repository, "approved_binding.need", "watering_need_approval", head, now, errors)
    window = binding["window"]
    if not isinstance(window, dict) or set(window) != {"evidence", "detail"}:
        _error(errors, "approved_binding.window", "requires approval receipt and bounded detail")
    else:
        detail = window["detail"]
        start = _time(detail.get("starts_at_utc")) if isinstance(detail, dict) else None
        end = _time(detail.get("ends_at_utc")) if isinstance(detail, dict) else None
        if (not isinstance(detail, dict) or set(detail) != {"window_id", "starts_at_utc", "ends_at_utc"} or not isinstance(detail.get("window_id"), str) or not SAFE_ID.fullmatch(detail["window_id"]) or not start or not end or not start < end or (end - start).total_seconds() > 24 * 3600):
            _error(errors, "approved_binding.window", "window must be positive, identified and no longer than 24 hours")
        elif end <= now:
            _error(errors, "approved_binding.window", "window has already expired")
        else:
            _evidence(window["evidence"], repository, "approved_binding.window", "window_approval", head, now, errors,
                      subject_equals={"window_id": detail["window_id"], "starts_at_utc": detail["starts_at_utc"], "ends_at_utc": detail["ends_at_utc"]})
    return binding, _canonical_hash(binding)


def validate(bundle: Any, repository: Path, *, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    now, errors = now.astimezone(timezone.utc), []
    if not isinstance(bundle, dict) or not _bounded(bundle):
        return _report("NOT_READY", None, ["input is not a bounded JSON object"], "UNKNOWN", [])
    if bundle.get("schema_version") != 2:
        _error(errors, "schema_version", "must be 2")
    if bundle.get("stage") != "PRE_ACTIVATION":
        _error(errors, "stage", "must remain PRE_ACTIVATION")
    mode = bundle.get("release_mode")
    if mode not in MODES:
        _error(errors, "release_mode", "is invalid")
    binding, binding_sha = _binding(bundle, repository, now, errors)
    head = binding.get("head") if isinstance(binding, dict) else ""
    ci = bundle.get("exact_head_ci")
    if not isinstance(ci, dict) or set(ci) != {"head", "conclusion", "evidence"} or ci.get("head") != head or ci.get("conclusion") != "success":
        _error(errors, "exact_head_ci", "requires a successful receipt for this exact head")
    else:
        _evidence(ci["evidence"], repository, "exact_head_ci", "ci_receipt", head, now, errors)
    assumptions = bundle.get("immutable_assumptions")
    if not isinstance(assumptions, dict) or set(assumptions) != {"training_calendar", "approved_watering"}:
        _error(errors, "immutable_assumptions", "training calendar and approved watering receipts are required")
    else:
        _evidence(assumptions["training_calendar"], repository, "immutable_assumptions.training_calendar", "calendar_approval", head, now, errors, subject_item="calendar")
        _evidence(assumptions["approved_watering"], repository, "immutable_assumptions.approved_watering", "watering_need_approval", head, now, errors, subject_item="need")
        if isinstance(binding, dict) and (assumptions["training_calendar"] != binding["calendar"] or assumptions["approved_watering"] != binding["need"]):
            _error(errors, "immutable_assumptions", "must use the exact bound calendar and watering receipts")
    safety = bundle.get("pre_activation_safety_evidence")
    if not isinstance(safety, dict) or set(safety) != set(PRE_ACTIVATION_GATES):
        _error(errors, "pre_activation_safety_evidence", "field, station, native schedule and controller ownership are required")
    else:
        for name in PRE_ACTIVATION_GATES:
            _evidence(safety[name], repository, f"pre_activation_safety_evidence.{name}", f"{name}_evidence", head, now, errors)
    physical = bundle.get("post_activation_evidence", {})
    if not isinstance(physical, dict) or not set(physical).issubset(set(POST_ACTIVATION_GATES)):
        _error(errors, "post_activation_evidence", "contains an unknown gate")
        physical = {}
    if mode == "FULL_UNATTENDED_ROLLOUT":
        for name in POST_ACTIVATION_GATES:
            if name not in physical:
                _error(errors, f"post_activation_evidence.{name}", "is required for full unattended rollout")
            else:
                _evidence(physical[name], repository, f"post_activation_evidence.{name}", f"{name}_evidence", head, now, errors)
    else:
        for name, record in physical.items():
            _evidence(record, repository, f"post_activation_evidence.{name}", f"{name}_evidence", head, now, errors)
    request = bundle.get("final_operator_approval")
    if not isinstance(request, dict) or set(request) != {"state", "one_shot", "approval_request_id", "binding_sha256"}:
        _error(errors, "final_operator_approval", "requires one pending one-shot request")
    elif request.get("state") != "PENDING" or request.get("one_shot") is not True or not isinstance(request.get("approval_request_id"), str) or not SAFE_ID.fullmatch(request["approval_request_id"]) or request.get("binding_sha256") != binding_sha:
        _error(errors, "final_operator_approval", "must remain PENDING and bind the exact request; this validator never accepts APPROVED or LIVE")
    pending = [name for name in POST_ACTIVATION_GATES if name not in physical]
    return _report("READY_FOR_FINAL_APPROVAL" if not errors else "NOT_READY", binding_sha, errors, mode, pending)


def _report(decision: str, binding_sha: str | None, errors: list[str], mode: Any, pending: list[str]) -> dict[str, Any]:
    return {"schema_version": 2, "decision": decision, "authorization": "NONE: offline validation never approves, deploys, or activates.", "release_mode": mode, "approved_binding_sha256": binding_sha, "operator_approval": "PENDING_ONLY" if decision == "READY_FOR_FINAL_APPROVAL" else "NOT_ACCEPTED", "pre_activation_complete": decision == "READY_FOR_FINAL_APPROVAL", "cannot_be_physically_verified_before_activation": pending, "full_unattended_rollout_requires": list(POST_ACTIVATION_GATES), "offline_limits": ["does not attest installed remote bytes or physical device behavior"], "errors": errors}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline final-release evidence check; never approves or deploys.")
    parser.add_argument("input", type=Path)
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    repository, input_path = args.repo_root.resolve(), args.input.resolve()
    try:
        input_path.relative_to(repository)
        local = True
    except ValueError:
        local = False
    if not repository.is_dir() or not local or not input_path.is_file() or input_path.stat().st_size > MAX_INPUT_BYTES:
        report = _report("NOT_READY", None, ["input or repository unavailable or outside bounds"], "UNKNOWN", [])
    else:
        try:
            report = validate(json.loads(input_path.read_text(encoding="utf-8")), repository)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            report = _report("NOT_READY", None, ["input is not valid UTF-8 JSON"], "UNKNOWN", [])
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.report:
        output = args.report.resolve()
        try:
            output.relative_to(repository)
        except ValueError:
            parser.error("--report must be rooted in --repo-root")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)
    return 0 if report["decision"] == "READY_FOR_FINAL_APPROVAL" else 2


if __name__ == "__main__":
    raise SystemExit(main())
