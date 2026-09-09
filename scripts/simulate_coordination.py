"""Generate reproducible local JSON/HTML evidence without external API calls.

Run from the repository: python -m scripts.simulate_coordination
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import subprocess
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from mower.coordination_simulation import Settings, example_scenario, propose, simulate_day


ROOT = Path(__file__).resolve().parents[1]
TZ = ZoneInfo("Europe/Berlin")
LABELS = {"MOWING": "Mähen", "RETURNING": "Heimfahrt", "CHARGING": "Laden", "PARKED": "Geparkt",
          "OCCUPANCY": "Belegung", "IRRIGATION": "Bewässerung", "DRYING": "Trocknung",
          "PRODUCTIVE_MOWING": "Produktives Mähen", "MINIMUM_WINDOW_OR_DOCK_CONFIRMATION": "Zu kurzes Mähfenster"}


def local(value: str) -> str:
    return datetime.fromisoformat(value).astimezone(TZ).strftime("%H:%M")


def comparison() -> dict:
    scenario, now, evidence = example_scenario()
    settings = Settings(enabled=True)
    baseline = simulate_day(scenario, scenario.need.original_start, settings)
    strategies = [{"key": "baseline", "label": "Ursprungstermin", "candidate_count": 1, **baseline}]
    for strategy, title in (("simple", "Bündelung beim Laden"), ("predictive", "Vorausschauender Vergleich")):
        suggestion = propose(scenario, now=now, evidence=evidence, settings=settings, strategy=strategy)
        if suggestion.selected_start is None:
            raise RuntimeError(f"The documented example has no safe {strategy} suggestion: {suggestion.blockers}")
        result = simulate_day(scenario, suggestion.selected_start, settings)
        strategies.append({"key": strategy, "label": title, "candidate_count": suggestion.candidate_count, **result})
    best = max(item["productive_mowing_minutes"] for item in strategies)
    for item in strategies:
        item["gain_vs_modelled_baseline_minutes"] = item["productive_mowing_minutes"] - baseline["productive_mowing_minutes"]
        item["additional_nonproductive_union_vs_best_model_minutes"] = best - item["productive_mowing_minutes"]
    sensitivity = []
    for charge_minutes in (60, 90, 120, 180):
        changed = replace(scenario, full_charge_minutes=charge_minutes)
        reference = simulate_day(changed, changed.need.original_start, settings)
        candidate = simulate_day(changed, now, settings)
        sensitivity.append({"assumed_full_charge_minutes": charge_minutes,
                            "baseline_productive_minutes": reference["productive_mowing_minutes"],
                            "bundled_productive_minutes": candidate["productive_mowing_minutes"],
                            "gain_minutes": candidate["productive_mowing_minutes"] - reference["productive_mowing_minutes"],
                            "baseline_final_battery_fraction": reference["final_battery_fraction"],
                            "bundled_final_battery_fraction": candidate["final_battery_fraction"]})
    try:
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        commit = None
    return {
        "schema_version": 1, "simulation_only": True, "live_execution_enabled": False,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(), "repository_base_commit": commit,
        "source_sha256": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                          for name in ("mower/coordination_simulation.py", "scripts/simulate_coordination.py",
                                       "tests/test_coordination_simulation.py")},
        "day_start_utc": scenario.start.isoformat(), "day_end_utc": scenario.end.isoformat(),
        "timezone": "Europe/Berlin", "planning_instant_utc": now.isoformat(),
        "settings": asdict(settings), "water_need": scenario.need.to_dict(),
        "assumptions": [
            "Synthetischer Modelltag 15.09.2026, durchgehend 00:00–24:00 MESZ; keine Betriebsdaten.",
            "Start mit vollem Akku; 180 produktive Minuten pro Ladung; 120 Minuten für eine vollständige Ladung.",
            "Lineares Energiemodell: Teilnachladung proportional zum Verbrauch; Heimfahrt dauert vier Minuten.",
            "Dock wird nach einer weiteren Minute mit zwei getrennten Beobachtungen als bestätigt modelliert.",
            "Verbindliche Belegung 16:30–20:30 inklusive bestehender Puffer; keine zusätzlichen Belegungspuffer.",
            "Ein angenommener bestätigter Wasserbedarf: fünf Zonen à 20 und zwei Zonen à 30 Minuten, insgesamt 160 Minuten.",
            "Fachlich zulässiges Startfenster 03:00–07:30 wird für dieses Beispiel angenommen; Ursprungstermin 04:30.",
            "Alle 150 Trocknungsminuten beginnen nach dem tatsächlichen letzten modellierten Zonenende.",
            "Stations- und Wegsicherheit, frische vollständige Belegung sowie Gerätehalte-/Suspendierungsbestätigungen sind simuliert.",
            "Im Basismodell kein Regen, kein EPOS-Fehler, keine API-Lücke und kein manueller Eingriff; solche Fehler werden separat getestet.",
            "Kein modellierter Zusatzwasserbedarf, keine Wasserreduktion, keine Aussage zu Litern oder Rasenbefahrbarkeit.",
            "Referenz ist ein modellierter Ablauf mit Ursprungstermin, keine exakte Wiedergabe der installierten FULL_FAILSAFE-Steuerung.",
        ],
        "strategies": strategies, "sensitivity": sensitivity,
        "recommendation": "Einfache Laderegel weiter im Schatten prüfen; 54 Kandidaten bringen in diesem Beispiel keinen Zusatzgewinn.",
    }


def render(data: dict) -> str:
    start = datetime.fromisoformat(data["day_start_utc"])
    elapsed = (datetime.fromisoformat(data["day_end_utc"]) - start).total_seconds()

    def bar(begin, end, key, label):
        left = 100 * (datetime.fromisoformat(begin) - start).total_seconds() / elapsed
        width = 100 * (datetime.fromisoformat(end) - datetime.fromisoformat(begin)).total_seconds() / elapsed
        title = f"{local(begin)}–{local(end)} · {label}"
        return f'<span class="bar {html.escape(key)}" style="left:{left:.5f}%;width:{width:.5f}%" title="{html.escape(title, quote=True)}" aria-label="{html.escape(title, quote=True)}"></span>'

    tracks, cards, table_rows = [], [], []
    for item in data["strategies"]:
        gain = item["gain_vs_modelled_baseline_minutes"]
        cards.append(f'<article class="metric"><p>{html.escape(item["label"])}</p><strong>{item["productive_mowing_minutes"]}<small> min produktiv</small></strong><span>{gain:+} min zur modellierten Referenz · {item["candidate_count"]} Kandidat(en)</span></article>')
        mower = "".join(bar(segment["start_utc"], segment["end_utc"], segment["mower"], LABELS[segment["mower"]]) for segment in item["timeline"])
        water = bar(item["irrigation_start_utc"], item["irrigation_end_utc"], "IRRIGATION", "Bewässerung")
        drying = bar(item["irrigation_end_utc"], item["dry_until_utc"], "DRYING", "Trocknung")
        occupied = bar("2026-09-15T14:30:00+00:00", "2026-09-15T18:30:00+00:00", "OCCUPANCY", "Verbindliche Belegung inklusive Puffer")
        rows = "".join(f'<div class="track-row"><span>{label}</span><div class="track">{values}</div></div>' for label, values in (("Mäher", mower), ("Wasser", water), ("Trocknung", drying), ("Belegung", occupied)))
        details = "".join(f'<tr><td>{local(segment["start_utc"])}–{local(segment["end_utc"])}</td><td>{LABELS[segment["mower"]]}</td><td>{LABELS.get(segment["primary"], segment["primary"])}</td><td>{html.escape(", ".join(LABELS.get(reason, reason) for reason in segment["all_reasons"])) or "—"}</td></tr>' for segment in item["timeline"])
        tracks.append(f'<section class="timeline-panel"><h2>{html.escape(item["label"])}</h2><div class="timeline-scroller"><div class="timeline-inner"><div class="axis"><span>00:00</span><span>06:00</span><span>12:00</span><span>18:00</span><span>24:00</span></div>{rows}</div></div><p class="note">Bewässerung {local(item["irrigation_start_utc"])}–{local(item["irrigation_end_utc"])} · früheste modellierte Trockenfreigabe {local(item["dry_until_utc"])}.</p><details><summary>Alle Zustandswechsel und gleichzeitigen Sperren prüfen</summary><div class="table-wrap"><table><thead><tr><th>Zeit MESZ</th><th>Mäher</th><th>Hauptgrund</th><th>Alle Gründe</th></tr></thead><tbody>{details}</tbody></table></div></details></section>')
        table_rows.append(f'<tr><th>{html.escape(item["label"])}</th><td>{item["productive_mowing_minutes"]}</td><td>{item["return_minutes"]}</td><td>{item["charging_minutes"]}</td><td>{item["parked_minutes"]}</td><td>{item["nonproductive_union_minutes"]}</td><td>{item["field_window_utilization_percent"]:.2f}%</td></tr>')
    assumptions = "".join(f"<li>{html.escape(value)}</li>" for value in data["assumptions"])
    legend = "".join(f'<span><i class="{key}"></i>{LABELS[key]}</span>' for key in ("MOWING", "RETURNING", "CHARGING", "PARKED", "IRRIGATION", "DRYING", "OCCUPANCY"))
    return f'''<!doctype html>
<html lang="de"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>SSV53 · Gemeinsame Planung in Simulation</title>
<style>
*{{box-sizing:border-box}}body{{margin:0;background:#f3f5f1;color:#163125;font:16px/1.55 system-ui,-apple-system,sans-serif}}main{{max-width:1160px;padding:32px 24px 60px;margin:auto}}.eyebrow{{font-size:12px;letter-spacing:.14em;font-weight:750;text-transform:uppercase;color:#456453}}h1{{font-size:clamp(28px,4vw,44px);line-height:1.15;letter-spacing:-.025em;margin:12px 0}}h2{{font-size:21px;margin:0 0 18px}}p{{max-width:940px}}.notice{{padding:16px 20px;border-left:5px solid #b48220;background:#fff4d8;border-radius:6px;color:#5a440f}}.metrics{{display:grid;grid-template-columns:repeat(3,1fr);gap:16px;margin:26px 0}}.metric,.timeline-panel,.panel{{background:white;border:1px solid #d5dfd6;border-radius:12px;padding:22px}}.metric p{{margin:0 0 12px;font-weight:650}}.metric strong{{display:block;font-size:34px;line-height:1.3}}.metric small{{font-size:14px;font-weight:500}}.metric span{{display:block;margin-top:10px;font-size:13px;color:#486353}}.legend{{display:flex;flex-wrap:wrap;gap:12px 20px;margin:20px 0;font-size:14px}}.legend i{{display:inline-block;width:14px;height:14px;border-radius:3px;margin-right:7px;vertical-align:-2px}}.timeline-panel{{margin-bottom:20px}}.timeline-scroller,.table-wrap{{overflow-x:auto}}.timeline-inner{{min-width:620px}}.axis{{margin-left:90px;display:flex;justify-content:space-between;color:#586d60;font-size:12px;font-variant-numeric:tabular-nums}}.track-row{{display:flex;gap:10px;align-items:center;margin:8px 0}}.track-row>span{{flex:0 0 80px;font-size:13px}}.track{{position:relative;height:25px;flex:1;background:repeating-linear-gradient(90deg,#edf1ec 0,#edf1ec calc(25% - 1px),#cdd9d0 calc(25% - 1px),#cdd9d0 25%);border-radius:4px;overflow:hidden}}.bar{{position:absolute;top:0;height:100%;border-right:1px solid #ffffff80}}.MOWING{{background:#24734a}}.RETURNING{{background:#bf7625}}.CHARGING{{background:#7379bb}}.PARKED{{background:#cbd1cc}}.IRRIGATION{{background:#2e88b8}}.DRYING{{background:#e0b95d}}.OCCUPANCY{{background:#a4505b}}.note,footer{{color:#53695b;font-size:14px}}details{{border-top:1px solid #e1e7e0;padding-top:12px}}summary{{cursor:pointer;font-weight:600}}table{{border-collapse:collapse;width:100%;font-size:14px;white-space:nowrap}}td,th{{padding:11px 12px;text-align:left;border-bottom:1px solid #e2e7e0}}thead{{background:#f1f5ee}}a{{color:#155b88}}.panel{{margin:20px 0}}li{{margin:8px 0}}.inspector{{padding:18px 0}}input[type=range]{{width:100%;accent-color:#24734a}}.state-output{{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;font-size:14px}}.state-output>div{{padding:12px;background:#eef4ec;border-radius:6px}}@media(max-width:720px){{main{{padding:20px 14px 40px}}.metrics,.state-output{{grid-template-columns:1fr}}.metric,.timeline-panel,.panel{{padding:16px}}.metric strong{{font-size:30px}}table{{font-size:13px}}}}
</style>
<main><div class="eyebrow">SSV53 · Prüfung vom 09.09.2026</div><h1>Laden und Bewässerung gemeinsam planen</h1>
<p>Modelltag Dienstag, 15. September 2026 · alle Uhrzeiten in Europe/Berlin (MESZ). Gleicher Wasserbedarf, gleiche Sportbelegung und gleiche Energieannahmen in allen drei Abläufen.</p>
<p class="notice"><strong>Offline-Simulation, keine Livefreigabe.</strong> Die 78 zusätzlichen produktiven Minuten sind ein Modellergebnis. Geräteverhalten, Wasserbedarf, Stationssicherheit und zulässige Verschiebung sind für dieses Beispiel angenommen.</p>
<div class="metrics">{"".join(cards)}</div>
<p><strong>Einfache Regel genügt im Beispiel:</strong> Der bereits notwendige Lauf beginnt beim bestätigten Laden um 03:05 statt um 04:30. Die vorausschauende Suche über 54 Kandidaten findet denselben Start. Die 150 Minuten Trocknung und alle 160 Minuten Bewässerung bleiben erhalten.</p>
<div class="legend">{legend}</div>{"".join(tracks)}
<section class="panel"><h2>Einen Zeitpunkt vergleichen</h2><div class="inspector"><label for="minute">Uhrzeit im Modell: <strong id="clock">09:00</strong></label><input id="minute" type="range" min="0" max="1439" value="540" step="1"><div id="states" class="state-output" aria-live="polite"></div></div></section>
<section class="panel"><h2>Vollständige Minutenbilanz</h2><div class="table-wrap"><table><thead><tr><th>Ablauf</th><th>Produktiv</th><th>Heimfahrt</th><th>Laden</th><th>Geparkt</th><th>Nicht produktiv, vereinigt</th><th>Fensterausnutzung</th></tr></thead><tbody>{"".join(table_rows)}</tbody></table></div><p class="note">Produktiv + Heimfahrt + Laden + Geparkt = 1.440 Minuten pro Tag. Belegung, Wasser, Trocknung und Laden überlappen; ihre Rohsummen dürfen nicht addiert werden. Die Fensterausnutzung bezieht sich auf 890 Minuten ohne Belegung, Bewässerung oder Trocknung. Beide Hauptvarianten enden mit derselben modellierten Restenergie.</p></section>
<section class="panel"><h2>Annahmen und Grenzen</h2><ul>{assumptions}</ul><p>Der Vergleich enthält keine Wasserzählermessung. Zusätzliche Ladeenergie wird berücksichtigt: mehr produktives Mähen führt auch zu mehr Ladezeit, die günstiger mit bestehenden Sperren zusammenfällt.</p><p><a href="coordination-comparison.json">Vollständige Daten als JSON</a> · <a href="coordination-simulation.md">Methodik, Tests und Einführungsvoraussetzungen</a></p></section>
<footer>Keine externen Bibliotheken, Geräteadapter oder Netzwerkanfragen. Dieses Artefakt ist eine prüfbare Ansicht, keine installierte App-Seite.</footer></main>
<script>
const model = {json.dumps(data, ensure_ascii=False).replace('</', '<\\/')};
const labels = {json.dumps(LABELS, ensure_ascii=False)};
const slider = document.getElementById('minute');
function inspect() {{
 const minute = Number(slider.value), instant = Date.parse(model.day_start_utc) + minute * 60000;
 document.getElementById('clock').textContent = String(Math.floor(minute/60)).padStart(2,'0') + ':' + String(minute%60).padStart(2,'0');
 const output = document.getElementById('states'); output.replaceChildren();
 for (const strategy of model.strategies) {{
  const segment = strategy.timeline.find(x => Date.parse(x.start_utc) <= instant && Date.parse(x.end_utc) > instant);
  const box = document.createElement('div'), title = document.createElement('strong'), text = document.createElement('p');
  title.textContent = strategy.label;
  text.textContent = labels[segment.mower] + ' · ' + (segment.all_reasons.length ? segment.all_reasons.map(x => labels[x] || x).join(', ') : 'kein Sperrgrund im Modell');
  box.append(title,text); output.append(box);
 }}
}}
slider.addEventListener('input', inspect); inspect();
</script></html>'''


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "docs" / "audit-2026-09-09")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    data = comparison()
    (args.output / "coordination-comparison.json").write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (args.output / "coordination-comparison.html").write_text(render(data), encoding="utf-8")
    print(json.dumps({"simulation_only": True, "output": str(args.output), "productive_minutes": {
        item["key"]: item["productive_mowing_minutes"] for item in data["strategies"]}, "sensitivity": data["sensitivity"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
