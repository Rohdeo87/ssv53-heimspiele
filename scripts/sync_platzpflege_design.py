"""Inline the approved presentation and vendored icon subset into the CMS templates."""
import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / "ui/platzpflege"


def sync(*, check=False):
    template = ROOT / "appack-platzwart-dashboard.html"
    html = template.read_text(encoding="utf-8")
    css = (UI / "design.css").read_text(encoding="utf-8")
    js = (UI / "design.js").read_text(encoding="utf-8")
    icons = json.loads((UI / "icons.json").read_text(encoding="utf-8"))
    js = js.replace("__PF_ICON_DATA__", json.dumps(icons, separators=(",", ":")))
    license_text = (UI / "LICENSE-lucide.txt").read_text(encoding="utf-8")
    js = "    /* Vendored Lucide icon subset (1.8.0).\n" + license_text + "\n    */\n" + js
    for label, content, anchor in (
        ("CSS", css, "  </style>"),
        ("JS", js, '    if(!isPlatzwart(profileJSON))'),
    ):
        start, end = "/* PF_DESIGN_" + label + "_START */", "/* PF_DESIGN_" + label + "_END */"
        block = start + "\n" + content + "\n" + end + "\n"
        if start in html:
            first = html.index(start)
            last = html.index(end, first) + len(end)
            html = html[:first] + block.rstrip("\n") + html[last:]
        else:
            if html.count(anchor) != 1:
                raise ValueError(f"Expected one {label} anchor")
            html = html.replace(anchor, block + anchor, 1)
    if check:
        if template.read_text(encoding="utf-8") != html:
            raise SystemExit("Dashboard design is out of date. Run scripts/sync_platzpflege_design.py")
        if template.read_bytes() != (ROOT / "appack-platzwart-dashboard.txt").read_bytes():
            raise SystemExit("Dashboard HTML and CMS copy differ")
        print("Dashboard sources and CMS copy are synchronized")
        return
    template.write_text(html, encoding="utf-8")
    (ROOT / "appack-platzwart-dashboard.txt").write_text(html, encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    sync(check=parser.parse_args().check)
