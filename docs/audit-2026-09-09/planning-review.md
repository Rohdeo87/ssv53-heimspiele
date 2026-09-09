# Unabhängige Prüfung der gemeinsamen Planung

Prüfdatum: 09.09.2026. Ausgangscommit: `9c2d0fc9c010b366cd49b58b8086b27f9d59a0dd`.
Prüfumfang: Quellcode, vorhandene Tests und lokale Offline-Simulation. Kein
Gerätezugriff, kein Deployment, kein Nachweis eines produktiven Schattenbetriebs.
Die Aussagen in `docs/adaptive-irrigation.md` über frühere Aktivierungen wurden
hier **nicht** als Betriebsnachweis übernommen.

## Festgestellter Ablauf

`mower/dry_run.py` lädt Konfiguration, Hydrawise-Status, Spiel-ICS,
Trainingsabsagen und Sonderbelegungen. `planner.create_plan` erzeugt Trainings-
und Bewässerungssperren, kombiniert sie mit bereits gepufferten Spiel-ICS und
führt Überschneidungen zusammen. `build_adaptive_plan` erhält diese verbundenen
Sperren und den aktuellen Hydrawise-Zonenplan. Der Schattenvorschlag fließt in
Diagnosedaten und das Beregnungsjournal; die Befehlsausführung verbleibt in
`full_failsafe.py`.

Der Repository-Stand `mower/config.json` enthält `00:00–00:00` und damit einen
ganzen lokalen Tag. `06:00–22:00` sind weiterhin Fallbackwerte in
`planner.create_plan`, keine belegte produktive Nachtgrenze. Ob die installierte
Konfiguration dem Repository entspricht, ist gesondert nachzuweisen.

## Belegte Befunde

| ID | Befund und Ursache | Nachweis im Ausgangsstand | Auswirkung | Sichere Maßnahme |
|---|---|---|---|---|
| P01 | Schattenplan bezieht Laden, Heimfahrt und Ladezustand nicht ein. | `adaptive_planner.build_adaptive_plan` akzeptiert keinen Mäher-/Ladezustand. | Keine nachgewiesene Optimierung produktiver Mähminuten. | Isolierter Vergleich mit explizitem Energiemodell und Ladeereignis. |
| P02 | `lost_dry_mowing_minutes` ist keine produktive Mähzeit. Belegungsüberschneidungen werden vor der anschließenden Überlappungssumme verworfen; diese Summe ist für zugelassene Kandidaten stets null. | Kandidatenschleife `adaptive_planner.py`, Konfliktprüfung vor `occupied_minutes`. | Zielfunktion bewertet hauptsächlich Wasser-/Trocknungsdauer und Zieluhrzeit; Ladeüberlappung bleibt unsichtbar. | Eigene Minutenbilanz mit Intervallvereinigung und getrennten physischen Mäherzuständen. |
| P03 | Derselbe vorhandene Zonenplan wird auf mögliche Starts im 48-Stunden-Horizont projiziert; ein verbindliches frühestes/spätestes Bedarfsfenster fehlt. | `horizon_hours`, `normalized_zones`, Kandidatenschleife. | Ein Vorschlag kann fachlich zu spät liegen; Shadow-only verhindert derzeit die Umsetzung. | Stabile Bedarfs-ID, explizit bestätigter Bedarf und freigegebenes earliest/latest-Fenster. Ohne diese Eingaben kein Vorschlag. |
| P04 | `plan_id` enthält `now`. | `identity` am Ende von `build_adaptive_plan`. | Gleicher Sachverhalt erhält bei jedem Zyklus eine andere ID; nicht als Ausführungs-/Deduplizierungsschlüssel geeignet. | Unveränderliche Bedarfs-ID und revisionsgesichertes Offline-Journal; bestehende ID nur als Snapshot-ID lesen. |
| P05 | `blocks` ist als `Iterable` deklariert, wird aber zuerst für `any(...)` und anschließend erneut für Belegungen durchlaufen. | `input_quality` und `occupancy_blocks`. | Bei Generatoren können Sperren aus dem Schattenplan verschwinden. Der aktuelle Aufrufer liefert eine Liste, daher hier kein belegter Livefehler. | Neue Bibliothek materialisiert Eingaben; bestehender Vertrag ist als Regression separat zu korrigieren. |
| P06 | Vorher verbundenes `irrigation+training` wird vollständig als Belegung gewertet. | `merge_blocks` setzt Quellen zusammen; `_source_parts` klassifiziert dann den gesamten Block. | Bestehende Bewässerungsränder können zulässige Alternativfenster im Schattenplan ausschließen. | Rohbelegungen mit unveränderten Puffern als eigene verbindliche Eingabe; keine Wasserblöcke als Sportbelegung umdeuten. |
| P07 | Starre 150 Minuten plus wetterabhängige Verlängerung; die Verlängerung zählt nasse Vorhersagepunkte als Stunden. | `_conservative_drying_extension_minutes`. | Nur Modell-/Empfehlungswert; keine Messung der Rasenbefahrbarkeit oder Wasserbilanz. | 150 Minuten im Vergleich unverändert lassen. Keine Verkürzung oder Wasserreduktion ohne fachlich verifizierte Eingangsdaten. |
| P08 | `ADAPTIVE_EXECUTION_ENABLED=true` wird aktiv abgewiesen. | `AdaptivePlannerSettings.from_mapping`, vorhandener Test. | Wirksame Grenze gegen versehentliche Geräteausführung. | Grenze beibehalten; neues Modul besitzt keine Geräteadapter und keine Laufzeitanbindung. |

`full_failsafe._new_schedule_override` besitzt bereits persistierbare
`source_plan_id`, Originalzeiten, verschobene Zeiten und Suspendierungsstatus.
Es verlangt für manuelle Anpassungen unter anderem einen vollständigen
Zonenplan, mindestens zwölf Minuten bis zum Ursprungslauf und eine bestätigte
Suspendierung. Ein neuer Liveoptimierer darf diese Kette nicht umgehen.
Die vorhandenen Akkugrenzen 60/90 Prozent unterscheiden im FULL_FAILSAFE-Pfad
Fortsetzung eines eigenen Mähauftrags von Neustart; dies ist keine Grundlage,
die Werte pauschal abzusenken.

## Priorisierung und Umsetzung innerhalb des Entwicklungsumfangs

1. **Jetzt, risikoarm:** neue Offline-Bibliothek, reproduzierbarer Tagesvergleich,
   Ausfalltests und persistentes Simulationsjournal. Keine Produktionseingabe wird
   geändert. Jede Wasserlaufzeit und die Mindesttrocknung bleiben erhalten.
2. **Vor Schattenintegration:** Rohbelegungsvertrag, echte Ladeereignisse und
   beobachtete Lade-/Heimfahrdauer bereitstellen; genehmigte Bedarfsfenster und
   Sicherheit von Station/Zonen/Anfahrtswegen bestätigen. Importqualität muss
   den ganzen vorgeschlagenen Konfliktzeitraum abdecken.
3. **Vor Livepilot:** Originalterminunterdrückung, Geräteparkierung und
   Wiederanlaufsperre unmittelbar am Gerät bestätigen; langlebige Befehlsabsicht
   und Wiederherstellung nach unklarer Antwort einbinden. Eine lokale
   SQLite-Transaktion beweist weder Azure-weite Befehlsfencing-Wirkung noch
   physische Suspendierung bei Hydrawise.

Die einfachste ausreichend gute Lösung ist zunächst eine einmalige Vorziehung
eines **bereits nötigen** Laufs beim bestätigten Laden. Sie wird nur vorgeschlagen,
wenn sie im genehmigten Fenster liegt und der berechnete Gewinn eine feste
Schwelle erreicht. Sie erzeugt keinen Wasserbedarf und verschiebt keine
Sportbelegung. Ein vorausschauender Vergleich darf weitere sichere Kandidaten
bewerten; vor dem Start gelten dieselben frischen Voraussetzungen erneut.
Ein einmal reservierter Bedarf wird nicht bei jeder Neuplanung verschoben.

## Grenzen und erforderliche Beobachtung

- Die bisherige produktive Planung kann ohne installierte Konfiguration und
  durchgehende Telemetrie nicht exakt nachgespielt werden. Der Vergleich bildet
  den Ablauf mit unverändertem Ursprungstermin als **modellierte Referenz** ab.
- Ladezeit, Akkureichweite, Dockbestätigung und räumliche Sicherheit sind im
  Tagesbeispiel explizite Annahmen, keine Hersteller- oder Betriebsnachweise.
- Ein Statusfehler darf eine bekannte physische Trocknungsfrist nicht verändern.
  Eine Lücke mit möglicher Bewässerung benötigt dagegen eigene Ereignisaufklärung
  oder eine konservative Sperre ab dem letzten möglichen Ende. Ein grüner
  Einzelstatus nach der Lücke beweist keinen trockenen Rasen.
- Bei angenommener, aber verlorener Befehlsantwort bleibt die Absicht unklar und
  wird nicht automatisch erneut ausgeführt. Keine neue Bedarfs-ID als Ausweg.
- Endgültiger Erfolg erfordert reale produktive Mähminuten, erfüllten Wasserbedarf,
  unveränderte Sicherheitsregeln sowie nachgewiesene Rest- und Ausfallrisiken.

Ergebnisse und Reproduktionsbefehle werden in `coordination-simulation.md`,
`coordination-comparison.json` und `coordination-comparison.html` abgelegt.

## Umsetzungsnachtrag

P05 wurde anschließend durch einmalige Materialisierung der Eingabe behoben.
P06 wurde ausschließlich im Schattenplan korrigiert: Eine verbundene Sperre wird
nur bei vollständigen, zusammenhängenden und widerspruchsfreien Herkunftsdaten
auf die ursprünglichen Belegungen zurückgeführt. Jede unvollständige Herkunft
oder enthaltene fail-closed-Sperre erhält den gesamten konservativen Block.
Verschachtelte fail-closed-Merkmale bleiben nun auch im Qualitätsstatus sichtbar.
Die 13 adaptiven Regressionstests sowie 29 neue Koordinationstests und die
bestehenden Mäh-/Beregnungsplantests bestanden zusammen (58 Tests).

Die HTML-Ansicht liegt prüfbar im Repository. Eine Browser-Sichtprüfung konnte
in dieser Teilprüfung nicht erfolgen: `getBrowser` meldete „No browser is
available“, die Browserinventur war leer. Es wird kein Screenshotnachweis oder
abgeschlossener mobiler Interaktionstest behauptet.
