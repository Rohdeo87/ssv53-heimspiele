# O01/T02: Gemeinsamer Trainingskalender und Winter-Schalter

09.09.2026. **Adapter und gemeinsame Laufzeitquelle entwickelt und offline
getestet; standardmäßig deaktiviert.** Die produktive Quelle wurde bewusst
nicht umgeschaltet. Die Nutzerentscheidung ersetzt feste Saison-Umschalttage
durch den zentralen Schalter. Schulferien werden nicht pauschal ausgenommen.
Die in Tests verwendeten freigegebenen Kalender sind ausschließlich synthetisch.

Die ergänzten Nutzerregeln stehen in [user-operating-rules.md](user-operating-rules.md).
Gesetzliche Brandenburger Feiertage unterdrücken dort ausschließlich Training;
Spiele bleiben erhalten. Schulferien sind eine getrennte fachliche Entscheidung.
Der persistente Winterumschalter wird zentral zum nächsten lokalen Mitternachts-
wechsel ausgewertet; konkrete Saison-Umschalttage werden nicht geraten.

## Tatsächlicher Datenweg

- `occupancy/service.py::_training_events` liest `occupancy/config.json` und
  die vom Aufrufer gewählte Saison. `build_occupancy_payload` und
  `build_training_occurrences` verwenden diese Ereignisse für Anzeige,
  Trainingspflege und serverseitige Terminidentifikation.
- `mower/planner.py::build_training_blocks` liest unabhängig
  `mower/config.json.training.weekly` samt `active_ranges`; `create_plan`
  fügt Spiele, Sonderbelegung und Wasser hinzu. `mower/schedule.py` existiert
  nicht. Der Steuerungsaufruf erfolgt über `mower/dry_run.py`.
- Beide Quellen enthalten die gleichen acht Sommer-Rasentermine. Die App hat
  zusätzlich neun Sommertermine auf Kunstrasen. Der Winterplan enthält 18
  Kunstrasentermine; die acht Sommer-Rasentermine bleiben im bisherigen
  Mäherplan über den gesamten Bereich 11.08.2026–09.07.2027 erhalten.
- `function_app.py::_trainer_occupancy_conflicts` wählt außerdem November bis
  Februar über eine Monatsheuristik als Winter. Dies ist keine nachgewiesene
  fachliche Saisonregel und wurde nicht in den neuen Kalender übernommen.
- Stabile `scheduleId` plus örtlicher Vorkommenstag verbinden bestehende
  Absagen mit dem Mähplan. Eine Freigabe erfolgt dort erst nach der gespeicherten
  Absagefrist. Sonderbelegungen und Verlegungen bleiben separate verbindliche
  Daten; dieser Adapter ersetzt sie nicht.

## Neue Dateien und Vertrag

- [Gemeinsames Modell, Validator und Projektionen](../../occupancy/training_calendar.py)
- [Lesender Quellen- und Intervallvergleich](../../occupancy/training_calendar_audit.py)
- [Deaktivierte gemeinsame Vorlage](../../occupancy/training_calendar.template.json)
- [Regressionen](../../tests/test_training_calendar.py)

Die Vorlage kopiert alle 17 Sommer- und 18 Winter-Wochenmuster sowie die drei
bereits vorhandenen Einzelabsagen unverändert aus der Belegungsquelle. Sie
enthält keine erfundenen Saisonzeiträume und keine automatisch abgeleiteten
Ferien. `enabled=false`, `season_periods=[]`, `holidays.reviewed=false` und ein
offener Freigabenachweis verhindern eine Aktivierung. Der vorhandene allgemeine
Gültigkeitsbereich wurde übernommen; er bestätigt keine Saisonzuordnung.

`resolve_training_calendar(...)` liefert entweder ein gemeinsames
`TrainingBatch` oder `batch=None` mit `retain_existing=true`. **Ein fehlendes
Batch bedeutet vorhandene Sperren beibehalten, nicht einen leeren Kalender.**
Dasselbe gilt bei abgelaufener Bereichsdeckung, defekter Datei, geänderter
Altquelle, mehrdeutiger Zeit oder unvollständiger Freigabe. Dateilesefehler muss
der künftige Aufrufer ebenso abfangen und sichtbar machen.

Ein gültiges Batch hat unveränderliche Ereignisse. `batch.app_events()` liefert
eine Kopie für die Anzeige; `batch.mower_blocks()` projiziert dieselben
blockierenden Rasenereignisse. Beide enthalten dieselbe nominale Zeit,
`occupancyStart`/`occupancyEnd`, ID, Revision und Inhaltsprüfsumme. Die Puffer
werden genau einmal als reale verstrichene Minuten angewendet; sie dürfen weder
unter 30 Minuten noch unter den bisherigen Mäherwert fallen. Der Platz wird aus
`resource_id` gelesen, niemals aus dem ID-Text wie `som-kr-ue40-mo` abgeleitet.

Die bestehende ID-Form `training:<saison>:<scheduleId>:<örtlicher Tag>` bleibt
über Revisionen und Neustarts erhalten. Inhaltsversion und ID sind getrennt.
Saisonbereiche müssen jeden Tag der versionierten Quelle genau einmal zuordnen.
Ferienausnahmen nennen konkrete Trainings-IDs, Datumsspannen und einen Beleg;
eine leere Ausnahmeliste ist erst nach ausdrücklicher vollständiger Prüfung
zulässig. Bestehende Einzelabsagen bleiben Teil des geprüften Inhalts.

`TrainingCancellation` nimmt nur serverseitig aufgelöste Absagen mit
`requested_at_utc` und `release_not_before_utc` auf. Die App kann eine ausstehende
Absage bereits kennzeichnen, während `blocking=true` die vollständige Sperre
bis zur Frist erhält. Widersprüchliche doppelte Absagedatensätze wählen die
spätere Freigabe. Die Klassen sind keine Berechtigungsprüfung; untrusted
Clientangaben dürfen diesen Eingang nicht befüllen.

Freigabestatus, Referenz, Freigabezeit und SHA-256 binden die fachlich geprüfte
Konfiguration. Das ist ein Releasebeleg aus vertrauenswürdiger Konfiguration,
keine digitale Signatur oder HTTP-Authentifizierung. Die bestehenden
serverseitigen Schreibrechte müssen erhalten bleiben. Jede inhaltliche
Änderung macht den bisherigen Inhaltshash ungültig; jede geänderte Altquelle
erfordert einen neuen Migrationsvergleich. Aufeinanderfolgende Versionen
müssen der spätere Publisher atomar und gegen Rückstufung sichern.

Zeitpunkte benötigen einen expliziten Offset; wiederkehrende Uhrzeiten sind
Berliner Vereinszeiten. Nicht existente oder doppeldeutige Trainingszeiten
werden abgewiesen. Nachttermine behalten den Starttag als Identität. Die Abfrage
berücksichtigt den vorherigen Ankertag für über Mitternacht laufende Termine
und den Endtag für vorgezogene Puffer. Diese angrenzenden Tage müssen ebenfalls
in der bestätigten Quelle enthalten sein; am Rand bleibt sonst der Bestand.

## Lesender Abweichungsreport

```powershell
python -m occupancy.training_calendar_audit
python -m pytest tests/test_training_calendar.py -q
```

Der Report schreibt ausschließlich JSON auf stdout. Exitcode 2 bedeutet
ungültig oder noch nicht aktivierbar; dies ist bei der ausgelieferten Vorlage
das erwartete Ergebnis. Kein Schreibzugriff, Import, Versand oder Gerätebefehl
wird ausgeführt.

Aktueller Quellenvergleich:

| Vergleich | Ergebnis |
|---|---|
| Kopierte Wochenmuster und Einzelabsagen | Stimmen mit der heutigen Belegungsquelle überein |
| Sommer-App / Mäher | Acht Rasenmuster, keine ID- oder Zeitabweichung |
| Winter-App / Mäher | Null Rasenmuster gegenüber acht beibehaltenen Sommermustern |
| Verbindliche Saisonzuordnung | 333 Tage ohne bestätigte Zuordnung |
| Ferienprüfung | Offen; keine Ferienregel angenommen |
| Migrations-/Aktivierungsstatus | Bestand beibehalten, keine gemeinsame Laufzeitquelle aktiviert |

Die Winterabweichung belegt einen Unterschied der Varianten, keine Erlaubnis
zur Entfernung dieser acht Sperren an einem bestimmten Tag.

`compare_training_blocks(batch, existing_training_blocks)` liefert für einen
konkreten geprüften Vorschlag zusätzlich vorhandene und vorgeschlagene
vereinigte Sperrminuten sowie hinzugekommene und entfernte Intervalle. Überlappte
Sperren zählen einmal. Der Report behauptet weder automatische Freigabe noch
produktive Mähzeit. Ein Test mit zwei überlappenden Trainingssperren zeigt:
240 Minuten Gesamtblock, nach einer ausdrücklich synthetischen Ferienausnahme
150 Minuten Restblock; die Differenz ist 90 statt der 150 Einzelblockminuten.

## Vorbereitung der tatsächlichen Integration

Die gemeinsame Laufzeitquelle ist im aktuellen Entwicklungsstand angebunden,
bleibt aber deaktiviert. Vor einer produktiven Umschaltung müssen mindestens
folgende Punkte gemeinsam erfüllt sein:

1. Kalender, Belegungsquelle und Mäherquelle aus einem versionierten,
   überprüften Paket laden. Eine deaktivierte oder nicht lesbare Quelle behält
   den Bestand und meldet den konkreten fehlenden Nachweis. Hash/Revision dürfen
   nicht nur im Frontend geprüft werden.
2. `occupancy/service.py` ersetzt ausschließlich seine Trainingsfolge durch
   `batch.app_events()`; Spiele und Sonderbelegung bleiben bestehen.
   `build_training_occurrences`, Konfliktprüfung, Verlegungsauflösung und
   Absageverwaltung müssen dieselbe datierte Quelle verwenden. Der Client-
   Saisonparameter darf keine verbindliche Belegung wegfiltern.
3. `mower/planner.py::create_plan` erhält die bereits gepufferten
   `batch.mower_blocks()` als alternative Trainingsfolge. Keine zusätzliche
   Pufferung und keine doppelte parallele Erzeugung alter und neuer Trainings-
   IDs. Bei `retain_existing=true` bleibt der bestehende Pfad aktiv. Spiele,
   Sonderbelegungen, manuelle Stopps und Wasserregeln bleiben nachgeschaltet.
4. Der Aufrufer bildet `TrainingCancellation` aus dem authentifizierten Store
   einschließlich der echten Freigabefrist. Beide Projektionen verwenden den
   gleichen Bewertungszeitpunkt; `cancelled` allein bedeutet keine Freigabe.
5. Vor Umschalten einen befehlsfreien Tagesvergleich über Saisonübergang,
   Ferien, Ausnahmen, Verlegungen und Nachtgrenzen abnehmen. Entfernte Intervalle
   einzeln gegen freigegebene Regeln prüfen. Backend, Appack-Saisonbedienung,
   Kalenderpaket und Zustandsversion müssen zusammen eingeführt werden.

## Aktuelle Aktivierungsvoraussetzungen nach der Nutzerentscheidung

- Feste zukünftige Umschalttage sind nicht mehr erforderlich. Der Schalter
  steuert die Saison ab dem nächsten Berliner Tag. Gesetzliche Brandenburger
  Feiertage entfernen ausschließlich Trainings; Spiele bleiben bestehen.
  Für Schulferien wird keine zusätzliche pauschale Ausnahme angenommen.
- Bestätigung der übernommenen Mannschaften, Platzzuordnungen, Zeiten und drei
  vorhandenen Einzelabsagen sowie Beleg und Zeitpunkt dieser Kalenderfreigabe.
- Prüfung aller tatsächlich entfernten Rasen-Sperrintervalle gegen diese Regeln;
  danach an den Inhalt gebundene Freigabe und erneuter Vergleich mit den dann
  gültigen Altquellen.
- Der persistente Schalter, datierte Übergangshistorie, CAS und anfrageweit
  unveränderliche Auflösung sind umgesetzt. Vor ACTIVE ist der nachgewiesene
  Ausgangsplan mit [initialize_training_control.py](../../scripts/initialize_training_control.py)
  einmalig zu speichern. Ein unbekannter Vortagsanker wird niemals erfunden.
  Bei Nachweisbeginn D wird ACTIVE frühestens am Berliner Tagesbeginn D+1
  eingeschaltet. So bleiben Nachttermine vom Vortag geschützt.
- Der [vorbereitete Kalender](../../occupancy/training_calendar.manual-control.candidate.json)
  enthält die unveränderten Wochenmuster, Einzelabsagen und Altquellen-Hashes,
  die geklärte Feiertagsregel und keine festen Saisonperioden. Sein Freigabebeleg
  bleibt offen. [prepare_training_calendar_approval.py](../../scripts/prepare_training_calendar_approval.py)
  erstellt erst mit echtem Beleg, Zeitpunkt und erwartetem Inhaltshash eine
  separate freigegebene Kopie. Es überschreibt keine vorhandenen Dateien.
- Der Bundlepfad `--manual-training-control` verlangt diese freigegebene Quelle.
  Der periodische Auto-Dispatch verwendet dauerhaft gespeicherte Freigabevariablen,
  damit der nächste Spielimport den gemeinsamen Kalender nicht wieder entfernt.
  Variablen und genaue Reihenfolge stehen im [Aktivierungsablauf](final-activation-runbook.md).

**O01 ist damit als sichere Entwicklung vorbereitet; die neue Quelle bleibt
deaktiviert und ist nicht live abgenommen.** Ein unverändertes Weiterlaufen
alter Winter-Rasensperren bleibt bis zur Klärung eine bekannte konservative
Betriebseinschränkung. Alle Verbraucher müssen denselben versionierten
`TrainingBatch` verwenden; Spiele und Sonderbelegungen bleiben nachgeschaltet.

Prüfung des früheren Adapterstands: **31 gezielte Tests bestanden**. Sie decken echte
Quellparität, ungültige/unfreigegebene Regeln, Quellenänderung, stabile IDs,
Neustart, beide Projektionen, einmalige Pufferung, verzögerte Absagen,
überlappende Ferienwirkung, Mitternacht und beide Sommerzeitwechsel ab.
Der damalige gemeinsame Lauf mit Trainingsabsagen, Belegungsservice,
Trainer-/Absage-API und bisheriger Mähplanung bestand mit **89 Tests**.

Der neue Schalter wurde zusätzlich mit Neustarts, zwei aufeinanderfolgenden
Wechseln, Nachtankern, beiden Sommerzeitwechseln, konkurrierender Bedienung,
Idempotenz, fehlender Historie und ohne Azure-SDK im OFF-Modus geprüft.
Die aktuelle Gesamtsuite und unabhängigen Reviews sind im
[Entwicklungsabschluss](final-preflight.md) verlinkt. Keine der Testfreigaben
ist ein produktiver Kalenderbeleg.
