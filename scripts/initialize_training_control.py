"""One-time, explicit CAS initialization for the manual training-plan control."""

from __future__ import annotations

import argparse
from datetime import date
import json
import os
from pathlib import Path
import sys
from typing import Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.request import (
    HTTPRedirectHandler,
    Request,
    build_opener,
)


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CONFIRMATION = "INITIALIZE_WINTER_TRAINING_CONTROL"
FUNCTION_ADMIN_URL = (
    "https://func-ssv53platzpflege-prod-q7kbw54s.azurewebsites.net/"
    "api/training-control/initialize"
)
HOST_KEY_ENV = "SSV53_TRAINING_INITIALIZER_HOST_KEY"
MAX_RESPONSE_BYTES = 65_536


class _NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _safe_error_code(data: bytes) -> str | None:
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(value, dict):
        return None
    code = value.get("code")
    if not isinstance(code, str) or not code or len(code) > 96:
        return None
    return code if all(character.isupper() or character.isdigit() or character == "_" for character in code) else None


def _function_request(
    *, url: str, host_key: str, inspect: bool, payload: dict | None,
    opener_factory=build_opener,
) -> dict:
    if url != FUNCTION_ADMIN_URL:
        raise ValueError("FUNCTION_ADMIN_URL_NOT_ALLOWED")
    if not host_key:
        raise ValueError("FUNCTION_ADMIN_HOST_KEY_REQUIRED")
    body = None if inspect else json.dumps(payload, separators=(",", ":")).encode("utf-8")
    request = Request(
        url,
        data=body,
        method="GET" if inspect else "POST",
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "x-functions-key": host_key,
        },
    )
    opener = opener_factory(_NoRedirects())
    try:
        with opener.open(request, timeout=30) as response:
            data = response.read(MAX_RESPONSE_BYTES + 1)
            if len(data) > MAX_RESPONSE_BYTES:
                raise RuntimeError("FUNCTION_RESPONSE_TOO_LARGE")
    except HTTPError as exc:
        data = exc.read(MAX_RESPONSE_BYTES + 1)
        code = _safe_error_code(data)
        detail = f" ({code})" if code else ""
        raise RuntimeError(f"Function antwortete mit HTTP {exc.code}{detail}.") from None
    except (URLError, TimeoutError, OSError):
        raise RuntimeError("Function-Transport fehlgeschlagen; es wurde nicht erneut gesendet.") from None
    try:
        result = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("Function-Antwort ist kein gültiges JSON.") from exc
    if not isinstance(result, dict):
        raise RuntimeError("Function-Antwort ist ungültig.")
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Initialisiert den persistenten manuellen Trainingsplan exakt einmal. "
            "Der Nachweisbeginn muss fachlich belegt sein."
        )
    )
    parser.add_argument("--initial-plan", choices=("Sommer", "Winter"))
    parser.add_argument("--history-valid-from", type=date.fromisoformat)
    parser.add_argument("--approval-reference")
    parser.add_argument("--request-id")
    parser.add_argument("--expected-state-revision", type=int)
    parser.add_argument("--confirmation")
    parser.add_argument("--function-admin-url")
    parser.add_argument("--inspect", action="store_true")
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    environment: Mapping[str, str] | None = None,
    function_sender=_function_request,
) -> int:
    args = build_parser().parse_args(argv)
    values = environment if environment is not None else os.environ
    if args.inspect:
        if any(value is not None for value in (
            args.initial_plan,
            args.history_valid_from,
            args.approval_reference,
            args.request_id,
            args.expected_state_revision,
            args.confirmation,
        )):
            raise SystemExit("--inspect darf keine Initialisierungswerte enthalten.")
        if args.function_admin_url != FUNCTION_ADMIN_URL:
            raise SystemExit("--inspect erfordert die exakt freigegebene --function-admin-url.")
        try:
            result = function_sender(
                url=args.function_admin_url,
                host_key=str(values.get(HOST_KEY_ENV) or ""),
                inspect=True,
                payload=None,
            )
        except (ValueError, RuntimeError) as exc:
            raise SystemExit(str(exc)) from None
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    if args.function_admin_url != FUNCTION_ADMIN_URL:
        raise SystemExit(
            "Die Initialisierung erfordert die exakt freigegebene "
            "--function-admin-url."
        )
    required = (
        args.initial_plan,
        args.history_valid_from,
        args.approval_reference,
        args.request_id,
        args.expected_state_revision,
        args.confirmation,
    )
    if any(value is None for value in required):
        raise SystemExit("Für die Initialisierung fehlen erforderliche Argumente.")
    if args.confirmation != CONFIRMATION:
        raise SystemExit(
            f"--confirmation muss exakt {CONFIRMATION} lauten."
        )
    payload = {
        "initialPlan": args.initial_plan,
        "historyValidFrom": args.history_valid_from.isoformat(),
        "approvalReference": args.approval_reference,
        "requestId": args.request_id,
        "expectedStateRevision": args.expected_state_revision,
        "confirmation": args.confirmation,
    }
    try:
        result = function_sender(
            url=args.function_admin_url,
            host_key=str(values.get(HOST_KEY_ENV) or ""),
            inspect=False,
            payload=payload,
        )
    except (ValueError, RuntimeError) as exc:
        raise SystemExit(str(exc)) from None
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
