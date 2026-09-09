# Konkreter Ablauf nach der abschließenden Freigabe

**Ablauf für die freigegebene betreute Einführung.** Die ausdrückliche
Nutzerfreigabe vom 09.09. liegt vor; der Nutzer übernimmt die Aufsicht und
nennt 19:00 Uhr als Trainingsende. Der tatsächliche Ausführungsstand steht
im [Einführungsprotokoll](live-introduction-progress.md). Dieses Dokument
bündelt die sieben Stufen aus [rollout-and-operations.md](rollout-and-operations.md)
und ist selbst kein Nachweis einer Installation oder Geräteabnahme.

## Festes Auslieferungsobjekt

- Repository `Rohdeo87/ssv53-heimspiele`, Entwicklungs-PR
  [47](https://github.com/Rohdeo87/ssv53-heimspiele/pull/47),
  Ziel `feature/azure-mower-migration`.
- Separater Importfix [PR 46](https://github.com/Rohdeo87/ssv53-heimspiele/pull/46)
  gegen `main`; geprüfter Kopf `efe932cbaf5186f050847a29347566782e891c20`.
  Zielköpfe unmittelbar vor einem Merge erneut lesen, Pflichtchecks und
  Mergebarkeit prüfen. Bei Änderung neu vergleichen, nicht den alten Nachweis übernehmen.
- Additive Veröffentlichung [PR 48](https://github.com/Rohdeo87/ssv53-heimspiele/pull/48)
  gegen `main`: nach PR 46 neu aufgesetzt, erneut geprüft und als
  `c4218fcbdda0fcd4b3ec5b4160b0bf93e7aa2c66` übernommen. Der anschließende echte
  Abruf/Publikationslauf ist im Einführungsprotokoll belegt.
- [PR 50](https://github.com/Rohdeo87/ssv53-heimspiele/pull/50) ergänzt den
  lesenden Vergleich des bestätigten Sommer-/Winterzustands. Mergecommit
  `03c95f75dfca8921532849f3721e4b237823da90`; die Schreibroute bleibt ACTIVE-only.
- Der ursprüngliche Lieferstand steht in
  [final-preflight-delivery.json](final-preflight-delivery.json). Maßgeblich für die
  inzwischen installierte Nachkorrektur sind [Quellnachweis](live-installed-shadow-source-evidence.json),
  [Deployment](live-shadow-deployment-evidence.json) und [Einführungsprotokoll](live-introduction-progress.md).
  Die prüfbare Appack-Vorlage ist `appack-platzwart-dashboard.txt`, bytegleich
  mit `.html`; ihre SHA-256 ist Bestandteil des Nachweises.
- Das neue CI erzeugt zusätzlich zum DRY_RUN-Paket ein getrenntes FULL_FAILSAFE-
  Quellpaket. Ein auf GitHub gespeichertes Archiv ist noch kein Remote-Build.
  Das Paket benötigt weiterhin den Python-3.12-Build in Azure.

## Fakten vor dem Gerätepiloten

| Tatsache | Benötigter konkreter Nachweis | Heutiger Stand |
|---|---|---|
| Training | Nutzerregel: Brandenburg-Feiertage ohne Training, Spiele erhalten; Winterplan über zentralen Schalter, Änderung ab nächstem lokalen Tag; gehashter Kalender | Sommer am 09.09. einmalig initialisiert; genehmigter Kalender veröffentlicht. SHADOW-Kandidaten für 10. und 15.09. live geprüft; ACTIVE frühestens 10.09. 00:00 Berlin, keine Umschaltung vorgemerkt |
| Bewässerungsbedarf | Bestehender nötiger Lauf und unveränderte Zonen/Dauern/Pausen; Start ab 03:30 und vollständiges Ende bis 08:00 Berlin | Zeitfenster durch Nutzer bestätigt; gelesener nächster Sollplan 04:30–07:10 passt nominell, Ausführung benötigt zusätzliche Reserve und erneute Prüfung |
| Station und Platz | Station sowie Fahrweg für alle sieben Zonen geschützt, freier Testplatz, lokale Stoppmöglichkeit und benannte Person | Nutzer bestätigt Station/Zufahrt außerhalb Beregnung, eigene Aufsicht und Mäher im Dock. Training bis 19:00, anschließend Puffer bis 19:30; keine Startzusage |
| Geräteverantwortung | Nativer Plan/aktive Aufträge und alle anderen Sender abgeglichen; keine unbekannte START-Wirkung | Beobachtungsfolgen ab 19:46 und 20:13–20:17 melden Station, 100 Prozent und FORCE_PARK. Dieser Parkzustand wird erhalten. Vollständige Herstellerwarteschlange und Verhalten beim späteren Start bleiben offen |
| Rückfallobjekt | Lesbare aktuelle Appsettings/Zustandssicherung, verfügbare bisherige Software, native Pläne und aktive Suspendierungen | 46 installierte Quellen gesichert; vollständiger Table-Export mit 27.005 Einträgen geprüft, zusätzliche Leseberechtigung wieder entfernt; vor Installation Aktualität erneut prüfen |
| Appack | Berechtigter CMS-Zugang zum Zieltemplate und echtes Appgerät für Sitzung-/Rollenprüfung | Platzpflege.tpl um 19:53 gespeichert und nach Reload mit geprüftem Quelltext verglichen; angemeldete native Appansicht gesondert zu bestätigen |

Fehlende Angaben bleiben fehlend. Die
[Freigabevorlage](release-approval-input.example.json) ist bewusst `NOT_READY`.
Der Offline-Prüfer bündelt schema- und hashgeprüfte Belege; er authentifiziert
deren Aussteller nicht und darf keine fehlende Vor-Ort-Prüfung ersetzen.

## Reihenfolge innerhalb einer ausdrücklich freigegebenen betreuten Einführung

1. **Platz und Geräte sichern.** Vor Deployment oder Timerabschaltung den Mäher
   tatsächlich stoppen/parken, die Station bestätigen und Bewässerungszustand
   sowie native Programme prüfen. Alle offenen Herstelleraufträge protokollieren.
   Eine Änderung von Azure-Schreibflags hält bereits laufende Geräte nicht an.
2. **Rückfall sichern.** Aktuelle Einstellungen, persistente Automatik-/Bedarfs-
   und Bewässerungsjournale sowie native Termine/Suspendierungen exportieren.
   Geheimnisse geschützt halten; keine Sicherung in Git. Verfügbarkeit des
   Rückfallpakets feststellen. Ein fehlendes Rückfallobjekt beendet die Einführung.
   Die geprüfte Quellsicherung liegt unter
   `dist/ssv53-installed-20260906-source-backup.zip`; Hash und Umfang sind in
   [installed-source-evidence.json](installed-source-evidence.json) festgehalten.
   Sie ersetzt weder Zustandssicherung noch das kontrollierte Abgleichen nativer
   Aufträge. Die alte Quellversion enthält die nachgewiesene Ausfalllücke und
   darf deshalb nur mit gesperrten automatischen Starts als Rückfall dienen.
3. **Kontrollierte Codeeinführung.** Alle Schreibinstanzen koordinieren; keine
   konkurrierenden alten Deploy-Workflows laufen lassen. Den geprüften Consumer
   vor dem additiven Importpublisher installieren. Schreibgates während der
   Installation geschlossen halten, während die Geräte bereits sicher stehen.
   Den genau gebundenen FULL_FAILSAFE-Quellstand auf
   `func-ssv53platzpflege-prod-q7kbw54s` in
   `rg-ssv53-platzpflege-prod` mit Remote-Build bereitstellen. Keine neue oder
   kostenpflichtige Infrastruktur dafür erzeugen.
4. **Installation belegen.** Erfolgreichen Remote-Build und Deployment-ID,
   registrierte 15 Funktionen (bisher 14 plus geschützte Kalenderinitialisierung), Manifest-Dateiprüfung und neue Versionsmeldung
   der tatsächlichen Instanz vergleichen. Eingabebundle und Kalender gegen den
   veröffentlichten Inhalt prüfen. Bei Hash-, Schema- oder Funktionsabweichung
   keine Start-/Bewässerungsfreigabe.
5. **App und befehlsfreie Beobachtung.** Geprüfte Appack-Datei veröffentlichen
   und am angemeldeten Appgerät Belegung, Zeiten, Datenfehler und Rollen prüfen.
   Zuerst neuen Anzeigepfad und den Planungsvergleich ohne Geräteausführung
   beobachten. Gemeinsamen Kalender samt bestätigter Nutzerregeln prüfen und
   ACTIVE setzen, bevor der Winter-Schalter bedient oder neue Automatik freigegeben wird.
   Den bestätigten Ausgangsplan zunächst einmalig speichern, wie unten beschrieben.
   ACTIVE frühestens am Berliner Tagesbeginn nach dem nachgewiesenen Anfangstag D
   einschalten; der Vortag wird für Nachttermine benötigt. Ein bestätigter späterer
   Wechsel gilt ab dem nächsten Berliner Tageswechsel. Belegung und App müssen denselben Stand zeigen.
   Ein einzelner grüner Zyklus genügt nicht: mindestens ein Ladeereignis, ein
   fälliger Wasserbedarf, ein Platztermin, Tageswechsel und eine kontrollierte
   Datenunterbrechung müssen ohne reale Konflikte ausgewertet sein.
6. **Begrenzter Pilot.** Nur das genehmigte Zeitfenster und den gebundenen Bedarf
   für die koordinierte Ausführung freigeben. Bestehende FULL_FAILSAFE-Gates
   bleiben erforderlich. Vor jedem Zonenstart frischen sicheren Stationszustand
   und unveränderte Belegung prüfen. Originaltermine müssen unterdrückt sein;
   keine zweite Bewässerung aus einem zweiten Bedarf oder Scheduler.
7. **Nachkontrolle vor Ausbau.** Vollständige sieben Zonen, echte Endzeit,
   150-minütige Trocknung, Ladeverlauf und mindestens zwei Sportbelegungen
   nachweisen. Produktive Mähminuten, Lade-/Heimfahrt-/Sperrzeiten, erfüllten
   Wasserbedarf und Fehler gemeinsam bilanzieren. Erst nach dieser Ereignisdeckung
   und ausdrücklich dokumentierter Abnahme den Betrieb erweitern. Nachtbetrieb
   bleibt von den tatsächlichen Platz-/Sicherheitsregeln abhängig.

Die Reihenfolge ist verbindlich; die Zeitdauer hängt vom tatsächlichen Wetter,
Bedarf und Spielplan ab. Ereignisse dürfen nicht künstlich ausgelöst werden,
um einen Bericht schneller grün zu bekommen. Ein erster betreuter Test und
unbeaufsichtigter Vollbetrieb sind getrennte Freigabestufen.

## Geplante Einstellungstrennung

| Option | Vorbereitung/erste Installation | Bedingung für spätere Änderung |
|---|---|---|
| Bestehende Start-/Bewässerungsschreibgates | Während gesicherter Installation geschlossen | Installation und sichere Geräte-/Platzlage nachgewiesen |
| `HYDRAWISE_STATUS_CACHE_MODE` | OFF | Kein Bestandteil dieses Gerätepiloten; 90-s-Kette unverändert |
| `HYDRAWISE_DASHBOARD_OBSERVATION_MODE` | OFF, anschließend gesondert AZURE_TABLE prüfen | Bestehende Tabellenrechte, frische Publikation, App ohne zusätzliche GETs nachgewiesen |
| `SHARED_TRAINING_MODE` | OFF während Installation, danach SHADOW/ACTIVE im betreuten Vergleich | Feiertags-/Winterregeln und alle Kalenderverbraucher verglichen; ACTIVE ist Pflicht für Freigabe mit den neuen Trainingsregeln |
| `WINTER_TRAINING_CONTROL_ENABLED` | false während Installation; mit PR 50 in SHADOW für den lesenden Vergleich true möglich | SHADOW liefert nur Kandidaten; App-Schalter und Schreibroute bleiben gesperrt. Bedienung erst mit ACTIVE und nachgewiesenem Vortag |
| `WINTER_TRAINING_INITIALIZATION_ENABLED` | false; ausschließlich während bestätigter einmaliger Einrichtung true, danach wieder false | ADMIN-Hostschlüssel, konkrete Freigabe, aktueller Revisionsvergleich; keine Geräteaktion |
| `COORDINATION_SHADOW_CAPTURE_ENABLED` | Nur nach versionsgebundener Installation einschalten | Vollständige Eingänge; ausschließlich Vergleich |
| `COORDINATION_EXECUTION_ENABLED` | false | Konkreter betreuter Pilot mit exakter Bedarfs-/Zeitfensterfreigabe |

Die Bewässerungsgrenze 03:30–08:00 ist im neuen Ausführungspfad verbindlich und
kein optionaler Optimierungsschalter. Native Hydrawise-Programme müssen ebenfalls
passen und gegen zusätzliche Originalausführung abgesichert sein. Das nominelle
Ende des Plans genügt nicht: Vor jedem Zonenstart wird die vollständige verbleibende
Folge einschließlich Pausen und Bestätigungsreserve erneut geprüft.

Kein Skript setzt diese Werte aufgrund eines positiven Offlineberichts automatisch.
`SSV53_Occupancy_Automation.yml` ist kein neutraler Paketbau: Änderungen an
`occupancy/config.json` auf dem Migrationsbranch können Deployment auslösen.
Auch der Main-Importmerge kann veröffentlichende Workflows starten. Diese
Nebenwirkungen sind Teil der finalen Freigabe, nicht bloße Entwicklungsschritte.
Der neue `SSV53_FULL_FAILSAFE_Release_Preflight.yml` prüft manuell einen exakten
bereits im Migrationsbranch enthaltenen Commit und führt keine Azure-Anmeldung aus.
Vor dem Merge liefert der bestehende PR-CI-Paketworkflow den Paketnachweis.

## Kalender und Winter-Schalter konkret vorbereiten

Die Freigabebelege und einmalige Sommerinitialisierung dieses Ablaufs wurden am
09.09. ausgeführt. Nicht erneut initialisieren. Das Einführungsprotokoll enthält
die tatsächlichen Revisionen, Veröffentlichungen und den verbleibenden ACTIVE-Schritt.
Die folgende Reihenfolge bleibt als reproduzierbarer Betriebsablauf erhalten.

1. Die übernommenen 17 Sommer- und 18 Winter-Wochenmuster, drei Einzelabsagen,
   Platzzuordnungen und den Ausgangsplan am örtlichen Tag D abgleichen.
   Feiertage betreffen nur Training; Schulferien bleiben ohne Pauschalausnahme.
   Kandidat: `occupancy/training_calendar.manual-control.candidate.json`.
   Inhaltshash gemäß `calendar_digest`:
   `bc9ccae4f33054cd29dcf4eed044d1b8095cac29c9215182f2646fdb3cdf1eec`.
2. Den tatsächlichen Beleg, Freigabezeitpunkt und Hash an
   `scripts/prepare_training_calendar_approval.py` übergeben. Die CLI erzeugt
   ausschließlich eine neue lokale freigegebene Kopie; ohne vollständigen Beleg
   oder bei geänderten Kalender-/Altquellenbytes bricht sie ab.
3. Für alle späteren automatischen Datenaktualisierungen die Repository-Variablen
   `SSV53_MANUAL_TRAINING_CONTROL_ENABLED=true`,
   `SSV53_TRAINING_CONTENT_SHA256`, `SSV53_TRAINING_APPROVAL_REFERENCE` und
   `SSV53_TRAINING_APPROVED_AT` mit genau derselben realen Freigabe setzen.
   Der bestehende sechs-stündliche Dispatcher übernimmt sie über den Zielworkflow.
   Auch spätere Spielimporte müssen beide Kalenderkopien im neuen Bundle enthalten.
4. `azure-runtime-config-rollout.yml` zunächst als `validate` ausführen. Die
   trainierten Verbraucher gegen die gleiche eingebettete Quelle vergleichen.
   Erst danach ausdrücklich bestätigtes `publish` verwenden. Ein Datenbundle
   installiert keine Steuerungssoftware. Bei ACTIVE darf kein Bundle ohne diese
   Quelle veröffentlicht werden; das würde die Freigabe absichtlich sperren.
5. Nach Installation den Schalter einmalig über
   `scripts/initialize_training_control.py` einrichten. Die CLI verwendet ausschließlich den geschützten HTTP-Zugang.
   Der lokale Azure-Benutzer konnte die Tabelle zunächst nicht lesen (403).
   Der danach ausdrücklich genehmigte, auf die Zustandssicherung begrenzte
   Table-Lesezugriff wurde wieder entfernt und erlaubt keine Schreibzugriffe.
   Für die Initialisierung ist ein separater, standardmäßig gesperrter Verwaltungsweg
   über die installierte Function vorbereitet:
   `https://func-ssv53platzpflege-prod-q7kbw54s.azurewebsites.net/api/training-control/initialize`.
   Dessen GET liest nur Revision und Trainingszustand; POST initialisiert exakt
   einmal mit vollständigem CAS. Die CLI erlaubt genau diese HTTPS-Adresse,
   keine Weiterleitung und keine automatische POST-Wiederholung.
6. Nur für diesen Schritt `WINTER_TRAINING_INITIALIZATION_ENABLED=true` setzen.
   Den bestehenden Masterkey ausschließlich flüchtig als
   `SSV53_TRAINING_INITIALIZER_HOST_KEY` bereitstellen; niemals in Appack,
   Nutzeroberflächen, Git, URL oder Logdateien. Der Azure-Host verlangt bei
   [AuthLevel.ADMIN den Masterkey](https://learn.microsoft.com/en-us/azure/azure-functions/functions-bindings-http-webhook-trigger#authorization-level).
   Lokale Tests prüfen das Binding; die Hostauthentifizierung ist nach Installation
   zusätzlich mit fehlendem/falschem Schlüssel lesend zu kontrollieren.
7. Erst lesendes `--inspect --function-admin-url <oben genannte URL>` verwenden.
   Danach `--initial-plan Sommer --history-valid-from D --approval-reference
   <echter Beleg> --request-id <eindeutige ID> --expected-state-revision <gelesener
   Stand> --confirmation INITIALIZE_WINTER_TRAINING_CONTROL` plus dieselbe URL
   übergeben. Falls der Ausgangsplan anders bestätigt wurde, Winter ausdrücklich
   wählen. Nach unklarer Antwort zuerst erneut lesen; keine neue Anfrage-ID erfinden.
   Ein exakter Replay derselben Initialisierung ist idempotent, eine abweichende
   zweite Einrichtung wird abgelehnt. Anschließend Gate wieder false setzen und
   flüchtigen Schlüssel entfernen.
8. Bei Nachweisbeginn D bleibt der gemeinsame Kalender bis zum Tagesbeginn
   D+1 außerhalb von ACTIVE. Mit PR 50 darf SHADOW vorher den bestätigten Zustand
   lesen; fehlende historische Tagesanker werden dabei nicht angenommen. Danach
   ACTIVE mit eingeschaltetem Trainings-Control gemeinsam setzen und heutigen Plan,
   Vortagsanker sowie Zukunftshorizont in App und Steuerung vergleichen. Es gibt keine
   angenommene historische Saison. Manuelle Stopps und Bewässerungsjournale werden
   bei Einrichtung und Umschalten erhalten.

Der neue Verwaltungsweg erhöht die Angriffsfläche begrenzt. ADMIN-Authentifizierung,
zusätzlicher Default-OFF-Schalter, striktes Schema, einmalige Initialisierung und
Versionsvergleich sind getrennte Schutzschichten. Ein kompromittierter Masterkey
bleibt ein hohes Restrisiko; seine Wirkung geht laut
[Microsoft-Dokumentation](https://learn.microsoft.com/en-us/azure/azure-functions/function-keys-how-to)
über diese einzelne Funktion hinaus. Er wird nicht an Appbenutzer verteilt.

## Abbruch und Rückfall

Sofort abbrechen bei Bewegung während Platz-/Wassersperre, fehlender Rückkehr,
unklarer START-Wirkung, ungeklärtem Ventil, fehlender Originalterminunterdrückung,
neuem Datenqualitätsverlust, konkurrierendem Sender oder Versionsabweichung.
Zuerst den Platz sichern und Geräte lokal stoppen; danach automatische neue
Starts/Bedarfsaufnahme sperren. Bereits gestartete Bewässerung kontrolliert
beenden und tatsächliches Ende erfassen. Trocknungsfristen und ungeklärte
Befehlsreservierungen erhalten. Native Zeitpläne und ausgesetzte Originaltermine
einzeln abgleichen; nicht pauschal reaktivieren.

Erst anschließend kompatiblen Code und Einstellungen zurücksetzen. Ein alter
Controller, der die neuen Sperrfelder nicht versteht, darf den aktuellen
Zustand nicht überschreiben. Nach Bedarf bleibt ein beaufsichtigter manueller
Betrieb bestehen. Die Wiederfreigabe verlangt die gleiche Prüfung von Gerät,
Plan und Datenalter; ein erneuter Deploy-Erfolg reicht nicht.

Azure Flex Consumption bietet hier keine Deployment-Slots; ein neues Staging-
System wäre ein eigener Infrastrukturauftrag. Ein Deployment-Rollback stellt
Appsettings nicht automatisch wieder her. Quellen:
[Bereitstellungsarten](https://learn.microsoft.com/en-us/azure/azure-functions/functions-deployment-technologies),
[Deployment-Rollback](https://learn.microsoft.com/en-us/azure/azure-functions/functions-rollback-deployments).
