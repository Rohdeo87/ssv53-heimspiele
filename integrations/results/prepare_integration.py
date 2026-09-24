"""Produce a REVIEWABLE source overlay without changing the existing app.

This is not a deployment ZIP. Existing builders/provenance checks still apply.
Usage from repository root:
  python integrations/results/prepare_integration.py --repo . --output dist/results-review
Optionally add --api-url https://CONFIRMED-HOST/api/ssv-results.
"""
from __future__ import annotations
import argparse
import ast
import difflib
import hashlib
import json
import re
import shutil
from pathlib import Path
from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parent
MARKER = "# SSV53 results: additive blueprint registration"
REGISTRATION = ("\n" + MARKER + "\nfrom results_blueprint import bp as ssv_results_blueprint\n"
                "app.register_functions(ssv_results_blueprint)\n")


def patch_function(source: str) -> str:
    if MARKER in source:
        if source.count(REGISTRATION) != 1:
            raise ValueError("Bestehende Ergebnisregistrierung ist verändert; manuell prüfen.")
        return source
    tree = ast.parse(source)
    candidates = [n for n in tree.body if isinstance(n, ast.Assign)
                  and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name)
                  and n.targets[0].id == "app" and isinstance(n.value, ast.Call)
                  and isinstance(n.value.func, ast.Attribute)
                  and isinstance(n.value.func.value, ast.Name)
                  and n.value.func.value.id == "func" and n.value.func.attr == "FunctionApp"]
    if len(candidates) != 1 or "ssv_results_blueprint" in source:
        raise ValueError("FunctionApp-Einstiegspunkt nicht eindeutig; keine automatische Änderung.")
    lines = source.splitlines(keepends=True)
    at = candidates[0].end_lineno
    if lines[at - 1] and not lines[at - 1].endswith("\n"):
        lines[at - 1] += "\n"
    changed = "".join(lines[:at]) + REGISTRATION + "".join(lines[at:])
    parsed = ast.parse(changed)
    filtered = [n for n in parsed.body if not (
        isinstance(n, ast.ImportFrom) and n.module == "results_blueprint"
    ) and not (
        isinstance(n, ast.Expr) and isinstance(n.value, ast.Call)
        and isinstance(n.value.func, ast.Attribute) and n.value.func.attr == "register_functions"
        and len(n.value.args) == 1 and isinstance(n.value.args[0], ast.Name)
        and n.value.args[0].id == "ssv_results_blueprint"
    )]
    if ast.dump(ast.Module(body=filtered, type_ignores=[])) != ast.dump(tree):
        raise ValueError("Bestehende Programmlogik würde verändert; Abbruch.")
    return changed


def configured_template(html: str, api_url: str | None) -> str:
    marker = 'const API_URL = "";'
    if html.count(marker) != 1:
        raise ValueError("API-Konfigurationsmarker nicht eindeutig.")
    if not api_url:
        return html
    parsed = urlsplit(api_url)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.port not in {None, 443} or parsed.query or parsed.fragment
            or parsed.path != "/api/ssv-results" or re.search(r"[\s<>]", api_url)):
        raise ValueError("Öffentliche HTTPS-Endpunkt-URL ohne Schlüssel erforderlich.")
    return html.replace(marker, "const API_URL = " + json.dumps(api_url) + ";")


def prepare(repo: Path, output: Path, api_url: str | None = None) -> dict:
    repo, output = repo.resolve(), output.resolve()
    if output.exists():
        raise ValueError("Ausgabeverzeichnis existiert bereits; kein Überschreiben.")
    source_path = repo / "function_app.py"
    original = source_path.read_text("utf-8")
    patched = patch_function(original)
    dependencies = (repo / "requirements.txt").read_text("utf-8")
    for line in (HERE / "requirements.txt").read_text("utf-8").splitlines():
        if line.strip() and not line.startswith("#") and line not in dependencies.splitlines():
            raise ValueError("Abhängigkeitsversion weicht vom geprüften Azure-Stand ab: " + line)
    html = configured_template((HERE / "appack/Ergebnisse_Tabellen.tpl").read_text("utf-8"), api_url)
    output.mkdir(parents=True)
    overlay = output / "source-overlay"
    overlay.mkdir()
    (overlay / "function_app.py").write_text(patched, "utf-8")
    shutil.copy2(HERE / "results_blueprint.py", overlay / "results_blueprint.py")
    shutil.copytree(HERE / "ssv_results", overlay / "ssv_results", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    (output / "function_app.patch").write_text("".join(difflib.unified_diff(original.splitlines(True), patched.splitlines(True), fromfile="a/function_app.py", tofile="b/function_app.py")), "utf-8")
    (output / "Ergebnisse_Tabellen.tpl").write_text(html, "utf-8")
    (output / "Ergebnisse_Tabellen.html").write_text(html, "utf-8")
    manifest = {"deployed": False, "existing_source_modified": False,
                "source_function_sha256": hashlib.sha256(original.encode()).hexdigest(),
                "template_endpoint_configured": bool(api_url),
                "warning": "SOURCE OVERLAY ONLY. Never deploy this directory as the complete existing Function App.",
                "files": {str(p.relative_to(output)): hashlib.sha256(p.read_bytes()).hexdigest()
                          for p in sorted(output.rglob("*")) if p.is_file()}}
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2), "utf-8")
    if source_path.read_text("utf-8") != original:
        raise RuntimeError("Quellstand wurde während der Vorbereitung extern verändert; Ergebnis verwerfen.")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--api-url")
    args = parser.parse_args()
    print(json.dumps(prepare(args.repo, args.output, args.api_url), indent=2))


if __name__ == "__main__":
    main()
