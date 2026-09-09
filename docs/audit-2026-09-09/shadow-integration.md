# Anschluss zum befehlsfreien Planvergleich

Entwicklungsstand vom 09.09.2026. Der neue Anschluss wurde lokal entwickelt und
mit Beispieldaten getestet. Er wurde nicht bereitgestellt oder im laufenden
Betrieb beobachtet. Es gibt weiterhin keinen Geräteadapter für das Vorziehen
der Bewässerung.

## Datenweg und Sicherheitsgrenze

`COORDINATION_SHADOW_CAPTURE_ENABLED=true` ergänzt vorhandene Zyklusdaten um
den vollständigen bereits berechneten Belegungshorizont, Quellnachweise und
Stoppsperren. Standard ist `false`. Es entsteht keine zusätzliche Hersteller-
oder Historienabfrage. Die Erfassung verändert weder die aktuelle Planung noch
Geräteprogramme oder den Steuerzustand. Die App erhält daraus keine Startzeit.

[coordination_shadow.py](../../mower/coordination_shadow.py) verwendet bei
zusammengeführten Sperren dieselbe strenge Prüfung ihrer Herkunft wie der
bisherige Schattenplaner. Unvollständige Kinderlisten behalten die ganze
übergeordnete Sperre. Unbekannte Quellen bleiben gesperrt. Ein manueller Stopp,
eine unklare Startwirkung oder fehlende Zustandsdaten verhindern einen Vorschlag.
Zwei unterschiedliche, zeitlich voranschreitende Herstellerbeobachtungen müssen
den verbundenen, fehlerfreien Mäher beim Laden zeigen.

Der lokale [Ablaufvergleich](../../scripts/replay_coordination_shadow.py) liest
ausschließlich Kopien dieser Eingänge. Er berechnet das erwartete Ladeende mit
derselben empirischen Auswertung wie die neue einfache App-Anzeige. Es werden
keine angenommenen Laderaten und keine späteren Messwerte verwendet. Ohne
ausreichende Ladehistorie entsteht kein Vorschlag. Die vorhandenen alten Logs
mit höchstens sechs bevorstehenden Sperren gelten ausdrücklich nicht als
vollständige neue Planungseingänge.

Für jeden Vergleich ist eine fachlich bestätigte Bedarfsinstanz erforderlich:
stabile ID, Bedarfsbeleg, Mäherzuordnung, zulässiges Startfenster, geprüfte Station
und Wege sowie vollständige Zonenfolge. Ursprüngliche Zonen, Startzeiten und
Dauern müssen exakt zum aktuellen Quellenstand passen. Regenanpassung,
Verlegung oder geänderte Zonen machen den bisherigen Beleg unbrauchbar. Ein
Ladevorgang allein erzeugt keinen Bedarf.

## Reproduzierbare Nutzung

Der private Export hat die Form
`{"schema_version": 1, "cycles": [<vollständige CycleResult-Datensätze>]}`.
Die Zyklen müssen eindeutig und zeitlich aufsteigend sein. Bei einer erneut
eingelesenen identischen Datei entsteht derselbe Vergleich; vorhandene
Ergebnisdateien werden nicht überschrieben. Der erste Vorschlag bleibt im
Ergebnis erhalten. Eine im Verlauf gesetzte echte Stoppsperre bleibt für die
weitere Auswertung wirksam. Dies ist ein Auswertungsprotokoll, kein Journal
auszuführender Geräteaktionen.

Die CLI begrenzt jede Eingabedatei auf 64 MiB und den Export auf 20.000 Zyklen.
Jede Beobachtung wird nur einmal normalisiert; die jeweils letzte Beobachtung
derselben Minute ersetzt die vorherige erst beim Erreichen dieses Zeitpunkts.
Es werden keine zukünftigen Zeilen vorab ausgewählt. Bereits anderweitig
ausgeschlossene Zyklen lösen keine erneute Ladehistorienauswertung aus. Für
verbleibende Auswertungen gelten höchstens fünf Millionen betrachtete
Historienzeilen und ein zwischen den Arbeitsschritten geprüftes Zeitbudget
von 30 Sekunden. Bei Überschreitung entsteht kein vollständiges Ergebnis und
keine neue Ausgabedatei. Größere Untersuchungen müssen gezielt auf einzelne
Bedarfsinstanzen aufgeteilt werden, mit deren erforderlicher vorheriger Historie.

Der unabhängige Review reproduzierte zuvor 2.001.000 Normalisierungen bei 2.000
Zyklen. Die Regressionstests belegen jetzt genau 2.000 Normalisierungen sowie
Abbruch bei Größen-, Zeit- und Arbeitsbudgetüberschreitung, auch während der
abschließenden Hashberechnung. Sie prüfen außerdem die minutenweise Auswahl
ohne Vorgriff auf einen späteren Messwert. Ein weiterer unabhängiger Vergleich
über 108 synthetische Zyklen mit drei abgeschlossenen Ladevorgängen und einem
positiven empirischen Vorschlag ergab dieselben Entscheidungen und denselben
ersten Vorschlag wie die frühere vollständige Präfixauswertung.

```powershell
python scripts/replay_coordination_shadow.py --cycles PRIVATE-cycles.json --approved-need PRIVATE-need.json --output PRIVATE-comparison.json
```

`PRIVATE-need.json` benötigt `schema_version=1`, `need_id`, `demand_reference`,
`mower_id`, `required=true`, `timing_window_approved=true`,
`station_and_paths_checked=true`, `earliest_start_utc`, `original_start_utc`,
`latest_start_utc`, `valid_until_utc` und `zones` mit `relay_id`, `run_seconds`
und `scheduled_start_utc`. Alle Zeiten enthalten einen UTC-Offset. Diese Angaben
sind betriebliche Eingänge; hier wurden keine realen Freigaben erfunden.

## Aussage des Vergleichs

Der Vergleich verschiebt die vorhandene Zonenfolge einschließlich ihrer
Zwischenräume. Wasserdauer und mindestens 150 Minuten Trocknung bleiben gleich.
Er vereinigt Laden, Bewässerung, Trocknung und Sport zeitlich, bevor er die
Differenz bildet. Überlappende Sperren werden nicht mehrfach gezählt.

Der neue Testfall nimmt einen bestätigten Bedarf an: ursprünglicher Start
05:30 Uhr, 40 Minuten Wasser, Trocknung bis 08:40 Uhr; Ladeende geschätzt
06:00 Uhr. Vorziehen auf 04:00 Uhr ergibt Trocknungsende 07:10 Uhr und **bis zu
90 freigewordene Flächenminuten**. Bestehen bereits 60 überlappende Sportminuten,
bleiben davon 30 Minuten. Das sind synthetische Daten am 15.09.2026 in Berlin.
Zusätzliche Ladeenergie, Heimfahrten und zu kurze Mähfenster sind in dieser
Obergrenze nicht abgezogen. Deshalb bleibt `productive_mowing_gain_minutes=null`.
Der ältere vollständige Tagesvergleich mit 642/720 produktiven Modellminuten
ist separat in [coordination-simulation.md](coordination-simulation.md) erklärt.

Auch ein positiver Vorschlag enthält unveränderlich `permission_to_start=false`
und `execution_available=false`. Bestätigte Unterdrückung des Ursprungstermins,
Verhinderung eines autonomen Mäherstarts und erneute Prüfung unmittelbar vor
dem Start bleiben Voraussetzungen eines noch zu entwickelnden Geräteadapters.
Es wurde keine tatsächliche Terminverschiebung oder Doppelunterdrückung behauptet.

## Noch erforderlich

Fachlich belegte Bedarfsfenster und Station-/Wegprüfung, Paketbereitstellung
für eine kontrollierte Erfassung, vollständige neue Eingangsdaten und der
Vergleich mit echten Ereignissen fehlen. Danach folgen die bestehenden
Stufen 5 und 6 mit Ereignisabdeckung und Aufsicht. Der Anschluss ersetzt diese
Nachweise nicht. Seine Eingangserfassung benötigt kein zusätzliches API-Budget;
eine separate periodische Ladehistorienabfrage wurde nicht eingeführt.
