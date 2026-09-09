# Abschluss der Entwicklungsprüfung und Vorbereitung der Freigabe

Stand 09.09.2026. Diese Fortsetzung ist zusammen mit dem
[Gesamtaudit](README.md) zu lesen. Sie ersetzt dessen ältere Aussagen zum
aktuellen Implementierungsstand. Der abschließende Commit-, Paket- und
Testnachweis steht in [final-preflight-delivery.json](final-preflight-delivery.json).
**Entwicklung, Simulation und lesende Produktionsprüfung sind getrennt von
Installation, Parallelbetrieb und Geräteabnahme.** Es erfolgte keine Liveänderung.

## Neu belegter Produktionsfehler

Die letzte vollständige Steuerungsmeldung stammt vom 09.09., 14:17:02 Uhr
Berliner Zeit. Ab 14:18 Uhr scheitert der Minutenzyklus: Sowohl `current` als
auch `previous` überschreiten das konfigurierte maximale Datenalter von
1.440 Minuten. Die Ausnahme tritt vor der Geräteabsicherung auf. Die Bezeichnung
„fail-closed“ im alten Fehlertext beschreibt deshalb keinen physischen Mäherstopp.
Die zeitgleiche Application-Insights-Prüfung zeigt neue Request-/Exception-
Datensätze; dies ist keine bloß stehengebliebene Loganzeige.

Der unabhängige Husqvarna-Abruf um 15:32 Uhr meldete ein verbundenes Gerät,
Automower 580 EPOS, `MOWING`, `IN_OPERATION`, Fehlercode 0 und 57 % Akku.
`calendar.tasks` war leer, während `planner.override.action=FORCE_MOW` galt.
Das belegt selbstständige Geräteaktivität während des Controllerausfalls.
Es identifiziert weder den Sender dieses Auftrags noch die gesamte Gerätewarteschlange.
Ein weiterer lesender Abruf um 16:09 Uhr meldete weiterhin `MOWING`, verbunden,
Fehlercode 0 und 42 % Akku. Dies ist eine zeitgebundene Beobachtung, keine Aussage
über den aktuellen Standort oder eine danach erfolgte Rückkehr.
Die vorherigen Meldungen mit Fehler 93 werden ausdrücklich nicht als aktueller
Gerätefehler ausgegeben. [Redigierte Produktionsnachweise](production-preflight-observations.json).

Die zugehörige Importstörung ist bereits in [PR 46](https://github.com/Rohdeo87/ssv53-heimspiele/pull/46)
behoben und live gegen FUSSBALL.DE gegengeprüft, aber noch nicht veröffentlicht.
Die Steuerung muss einen Ausfall ihrer Eingangsdaten trotzdem selbst behandeln.
Das maximale Datenalter wird nicht verlängert; Spiele und Sperren werden nicht
aufgrund des Ausfalls entfernt.

Ein abschließender lesender Abruf um **17:10 Uhr** meldete `PARKED_IN_CS`,
`RESTRICTED`, Fehlercode 0, 61 % Akku und `NOT_ACTIVE`. Die tatsächliche
Stationsmeldung ersetzt weder den Nachweis einer dauerhaft wirksamen Parksperre
noch eine erfolgreiche Wiederherstellung der zentralen Steuerung.

## Technische Umsetzung

- `InputUnavailable` unterscheidet erwarteten Quellenverlust von sonstigen
  Programmfehlern. Der FULL_FAILSAFE-Einstieg erzeugt einen nachvollziehbaren
  Schutz-/Eskalationszyklus statt vor allen Schutzentscheidungen abzubrechen.
- Der Ausfallpfad gibt weder Fläche noch START oder Bewässerung frei. Er erhält
  Stoppsperren, Bewässerungsjournal und ungeklärte Befehlswirkungen. Ein zulässiger
  Schutz-PARK benötigt eindeutig zugeordnetes Gerät, aktuelle Herstellerdaten,
  bestehende Schreibfreigabe und eine vorherige persistente Reservierung.
  Annahme durch den Hersteller bedeutet weiterhin keine bestätigte Rückkehr.
- Ohne eindeutig sicheren Zustand bleibt die Auflösungsbedingung eine frische
  und vollständige Eingabe plus geklärte Geräte-/Befehlslage. Ein abgelaufener
  oder unbestätigter Sendeversuch wird nicht blind wiederholt. Eine vollständig
  ausgefallene Zentrale oder ein verspätet wirkender Herstellerauftrag bleibt
  ein technisches Restrisiko und benötigt lokale Eingriffsmöglichkeit.
- Für die App publiziert ausschließlich der bestehende direkte Steuerungsabruf
  optional einen geprüften Hydrawise-Stand in einer getrennten Partition der
  bereits vorhandenen Zustandstabelle. Die App liest diesen Stand ohne weiteren
  Herstellerabruf. Die Steuerung liest diesen Anzeigespeicher niemals.
  `HYDRAWISE_DASHBOARD_OBSERVATION_MODE=OFF` ist Standard; die vorbereitete Option
  heißt `AZURE_TABLE`. Leere, alte, widersprüchliche oder fehlerhafte Daten
  deaktivieren die Bedienung. Publikationsfehler verhindern keine Schutzentscheidung.
- Der bisherige allgemeine Hydrawise-Cache bleibt im Gerätebetrieb gesperrt.
  Die unveränderte 90-Sekunden-Revalidierung wird nicht aufgeweicht. Der neue
  Anzeigepfad vermeidet diese Abhängigkeit, ohne eine wiederholte Beobachtung
  als neue Gerätebestätigung zu zählen.
- Die App zeigt „Belegungsplan nicht aktuell“, eine konkrete Vor-Ort-Handlung
  und „Noch offen“. Die Belegungskarte behauptet bei fehlender Quelle nicht
  „Platz ist frei“. Aktuelle Gerätedaten können daneben weiterhin angezeigt werden.
  [Prüfbare Vorschau mit Beispieldaten](../ui-2026-09-09/final-preflight/appack-preview.html),
  [mobil](../ui-2026-09-09/final-preflight/stale-plan-390.png),
  [klare Zeitansicht](../ui-2026-09-09/final-preflight/charging-390.png).

## Ergänzte Priorisierung und Risiken

Die anschließend vom Nutzer verbindlich festgelegten Betriebsregeln stehen in
[user-operating-rules.md](user-operating-rules.md): gesetzliche Feiertage in
Brandenburg ohne Training, Spiele unverändert; zentraler Winter-Schalter mit
Wirksamkeit ab nächstem lokalen Tageswechsel; Bewässerungsstart frühestens
03:30 Uhr und vollständiges Ende spätestens 08:00 Uhr. Damit entfallen die
früher offenen Saisonumschalttage und das bisher nur angenommene Zeitfenster.
Schulferien sind keine pauschalen Trainingsabsagen. Bestehende Ausnahmen bleiben
Bestandteil des gemeinsamen Kalenders.

Die vollständige Maßnahmen- und Risikotabelle bleibt in
[improvements-and-risks.md](improvements-and-risks.md). Ergänzungen:

| ID | Bereich | Problem und Ursache | Nachweis | Verbesserung | erwarteter Impact | Aufwand | Abhängigkeiten | Änderungsrisiko | Risikominderung | Abnahmekriterium | Priorität |
|---|---|---|---|---|---|---|---|---|---|---|---|
| S04 | Steuerung | Veraltetes Datenbundle wirft vor Schutzparken; laufendes Gerät wird nicht abgesichert. | Azure-Ausnahme plus direkter Herstellerstatus; reproduzierbare Fehlerprüfung | Typisierter Ausfallpfad mit persistenter Schutzentscheidung | Hoher Sicherheits- und Zuverlässigkeitsnutzen; kein Mähzeitgewinn beziffert | M | Bestehende Gates, eindeutige Identität, frische Telemetrie, CAS | Falsche Parkierung, verlorene Antwort, später wirksamer Start | Keine Flächenfreigabe; aktuelle Uhr/Reservation; getrennte Bestätigung; Eskalation | Quellenverlust, zwei Writer, Neustart, verlorene Antwort, Ladung, manueller Stopp und Wiederherstellung bestanden | P0 |
| A02 | App-Abfragen | App und Minutenzyklus verursachen getrennte Hydrawise-Abfragen. | Reale Aufrufpfade; Integrationstest | Vom direkten Controllerabruf veröffentlichter Anzeigestand | Im Integrationstest 1 Herstellerabruf für 1 Controllerlauf + 3 Appabrufe statt 4; kein Geldbetrag oder Livegewinn behauptet | M | Vorhandene Tabelle/Identität, sieben Relays | Alter Stand als neu behandelt; Storage-Ausfall blockiert Controller | Quellzeit unverändert, Höchstalter, CAS, Whitelist; App ohne HTTP-Fallback, Publisherfehler isoliert | App liest wiederholt ohne zusätzliche GETs oder Zustandsmutation; Controller bleibt direkt | P1 |
| U02 | Anzeige | Alter Fehlertext versprach trotz Controllerausfall eine sichere Gerätesperre. | Quellcode und neue Vorschau | Klare Fehlermeldung, Vor-Ort-Handlung, unbekannte Belegungszeiten | Vermeidet irreführende Betriebsentscheidung | S | UI und Backend gemeinsam | Alte App zeigt weiterhin frühere Texte | Paket-/Templatebindung und Prüfung am tatsächlichen Appgerät | Kein sicherer Mäherstopp oder freie Fläche allein aus Datenfehler behauptet | P0 |
| V02 | Auslieferung | Bisheriges CI-Artefakt ist nur das befehlsfreie Quellpaket; ältere Installer testen nur unittest | Workflowprüfung | Zusätzliches FULL_FAILSAFE-Artefakt aus exakten Git-Bytes mit netzwerkgesperrtem Import und vollständigem pytest | Prüffähiges späteres Auslieferungsobjekt; weniger Versionsverwechslung | S | Grüne Prüfung des finalen Kopfes | Paket wird mit installierter Version verwechselt | Build, Artefakt, Deployment und Gerätewirkung separat nachweisen | Paketdateien/Manifest gegen Commit geprüft, 15 Funktionen importierbar, kein Deployment | P0 |
| S05 | Mäherstart | OAuth oder Zustandsabfrage kann einen zunächst zulässigen Start über sein Zeitfenster hinaus verzögern. | Fehlerprüfungen mit verzögerter Antwort und parallel gesetztem Stopp | Erneute Prüfung nach OAuth unmittelbar vor POST; Restdauer auf volle zulässige Minuten begrenzen | Schließt nachgewiesene Softwarelücke, kein Mähzeitgewinn behauptet | M | Exakte persistente Reservation und aktuelle Beobachtungen | Ablehnung hinterlässt ungeklärten Auftrag | Schutzreservierung erhalten; Abgleich vor Wiederanlauf; Uhr erst nach Speicherabruf lesen | Kein POST nach Frist oder zwischenzeitlichem Stopp; verlorene Antwort bleibt gesperrt | P0 |
| T02 | Training/App | Kalender und umschaltbare Wintersaison brauchen dieselbe verbindliche Grundlage. | Nutzerentscheidung, Feiertagsgesetz, Verbraucher- und Neustarttests | Persistenter zentraler Schalter; Feiertage ausschließlich aus Trainings entfernen | Verlässliche Belegung, einfache Bedienung; frei werdende Zeit nicht pauschal beziffert | M | Gemeinsamer Kalender ACTIVE und aktuelle Daten | Umschalten könnte laufende Belegung entfernen; parallele Änderung verloren | Wirksamkeit erst ab nächstem Berliner Tag, Vergleich der Trainingsrevision und CAS, keine leere Ersatzbelegung | Alle Verbraucher verwenden gleiche Auflösung; Spiele am Feiertag erhalten; Stoppzustand unverändert | P0 |
| W02 | Bewässerung | Alte Aufnahme/Bedarfsfreigabe garantiert keinen vollständigen Lauf innerhalb 03:30–08:00. | Nutzerregel, Zeitfenster- und Ausführungstests | Aufnahme und tatsächlichen Zonenstart mit vollständiger Restfolge, Pausen und Bestätigungsreserve prüfen | Verhindert planmäßige frühe/späte Bewässerung; Versorgung wird nicht durch Kürzung passend gerechnet | M | Unterdrückung nativer Termine, Stationsnachweis, unveränderter Bedarf | Lauf wird zu spät abgewiesen; native Anlage kann unabhängig starten | Frühzeitige Planung, erneute Prüfung vor Senden; fehlenden Bedarf ausweisen; native Programme im Pilot prüfen | Vor 03:30 und bei nicht bis 08:00 passender Restfolge kein Start; keine Doppelbewässerung | P0 |
| T03 | Einführung | Lokaler Table-Zugriff liefert 403; direkte Managed-Identity-CLI ist am Arbeitsplatz nicht ausführbar | Lesender Zugriffsversuch, isolierte Route-/CLI-Tests | Einmalige Einrichtung über bestehende Function-Identität und ADMIN-Route; GET nur Trainingsstand/Revision | Ausführbarer Einführungsweg ohne neue Tabellenrollen oder Geräteaktionen | S | Installierte neue Function, vorhandener Masterkey, echte Freigabe | Zusätzlicher Verwaltungsendpunkt | Default-OFF, ADMIN, striktes Schema, CAS und exakte URL ohne Redirects; anschließend wieder sperren | Ohne Gate kein Zugriff; keine Gerätebefehle; Replay verändert Zustand nicht erneut | P1 |

| Risiko | Eintritt | Schaden | Erkennung | Prävention | Wiederherstellung | Restrisiko |
|---|---|---|---|---|---|---|
| Eingangsdaten veralten, laufender Mäher bleibt aktiv | Aktuell belegt | Sehr hoch | Typisierte Ausfallzyklen; fehlende erfolgreiche Zyklen; direkte Telemetrie | Importfix und unabhängiger Schutzpfad | Platz sichern, tatsächlichen Mäherstopp prüfen, frische Quelle wiederherstellen, offene Befehle klären | Zentrale kann vollständig ausfallen; Hersteller kann verspätet ausführen |
| Anzeigespeicher ist alt oder unerreichbar | Offline injiziert, Livehäufigkeit unbekannt | Mittel | Quellzeit, Datenqualität und fehlende Snapshotpublikation | App ohne Herstellerfallback; direkte Steuerung unabhängig | Erreichbarkeit prüfen; Publikation wiederherstellen, neue Quellbeobachtung abwarten | Kurzzeitig unbekannte Anzeige; manuelle Aktion braucht eigene frische Prüfung |
| Empfangener PARK wirkt nicht oder erst spät | Schnittstellenbedingtes Restrisiko | Sehr hoch | Weiterhin bewegtes Gerät; unbestätigte Reservation | Keine blinde Wiederholung, sofortige lokale Eingriffsmöglichkeit | Vor Ort stoppen, ausstehende Herstelleraktionen abgleichen | Cloud-API bietet keine transaktionale Ausführungsfrist |
| Freigabeprüfer wird als automatische Erlaubnis missverstanden | Möglich | Hoch | Bericht enthält `authorization=NONE` | Nur lokale Schema-/Hash-/Fristprüfung; keine Aktivierungsfunktion | Geänderte Belege/Commit erneut prüfen, menschliche Freigabe getrennt | Genannte Belegaussteller sind nicht kryptografisch authentifiziert |
| Winter-Schalter entfernt Belegung am falschen Tag | In Tests injiziert | Hoch | Heutiger Plan und vorgemerkter Wechsel getrennt sichtbar | Berliner Tagesgrenze, Feiertage nur Training, unveränderter Snapshot pro Anfrage | Aktualisieren, gespeicherten Wechsel prüfen; Ausfall hält Freigabe an | Falsche vereinseigene Trainingszeiten sind ohne fachlichen Abgleich nicht erkennbar |
| Bewässerungsende überschreitet trotz Planung 08:00 | Geräte-/Kommunikationsfehler möglich | Hoch | Echte Zonen-Endmeldungen und Uhrzeit vergleichen | Restlaufprüfung vor jedem Start einschließlich unveränderter Pausen und Reserve | Lokale Kontrolle und kontrolliertes Beenden; tatsächliches Ende und unerfüllten Bedarf festhalten | Ventile und native Geräteprogramme lassen sich mit Cloudbefehlen nicht zeitgenau garantieren |

## Tatsächliche Infrastruktur und Geräteanbindung

Die angegebene Function App ist vorhanden, läuft mit Python 3.12 und hat
14 registrierte Funktionen. Der Mäh-Timer läuft minütlich. In 1.367 vollständigen
Steuerdatensätzen des abgefragten 24-Stunden-Zeitraums wurde eine Hostinstanz
beobachtet. Das schließt andere Sender und Geräteaufträge nicht aus.
Die neue Koordination, gemeinsame Trainingsquelle und neuen Cacheoptionen
sind in den gelesenen produktiven Einstellungen nicht aktiviert.

Die Deploymentmetadaten nennen den 06.09., 07:35–07:36 Uhr als aktives
erfolgreiches Deployment. Blobzugriff und der frühere SCM-Pfad scheiterten.
Um 16:48 Uhr konnten jedoch über den von Azure selbst angegebenen `admin/vfs`-
Lesepfad **alle 46 installierten Quelldateien** abgerufen werden. Alle 46 stimmen
bytegenau mit ihrem installierten Paketmanifest überein. Nach ausschließlich
CRLF/LF-Normalisierung stimmen sie vollständig mit dem Migrationsstand
`9c2d0fc9c010b366cd49b58b8086b27f9d59a0dd` überein; nur drei sind ohne diese
Normalisierung bytegleich zum Git-Inhalt. Damit ist der zuvor offene
Quellvergleich geschlossen, ohne unterschiedliche Dateihashes gleichzusetzen.
[Dateiweise Nachweise und Rückfallpaket](installed-source-evidence.json).

Die abgerufenen Quellbytes und das Manifest sind zusätzlich in einem lokalen
Rückfall-Quellarchiv gesichert. Es enthält keine laufenden Steuerungszustände,
Geräteprogramme oder installierten Dependency-Wheels und benötigt Remote-Build.
Die neuen Änderungen und Versionsmeldungen dieses PR sind noch nicht installiert.
Für die lesende Prüfung wurden bestehende Rechte verwendet und keine geändert.

Hydrawise antwortete um 15:44:52 Uhr mit genau den sieben erwarteten Zonen,
keiner laufenden Zone und `nextpoll=60`. Die Zonen-Sollfolge für den 10.09.
ist 04:30–07:10 Uhr: fünfmal 20 und zweimal 30 Minuten, zusammen 160 Minuten.
Die Antwort enthält keine Sensoreneinträge. Das beweist weder fehlende physische
Sensoren noch die tatsächliche Regenabschaltung oder abgegebene Wassermenge.
Ein vorheriger 404 in der Prüfung entstand durch eine noch nicht aufgelöste
Key-Vault-Referenz des Controller-Identifiers; er wird nicht als Anlagenfehler gewertet.

Der [Hersteller-Support](https://www.hunterirrigation.com/support/hydrawise-api-information)
verlinkt die [REST-API 1.6](https://www.hunterirrigation.com/sites/default/files/2026-05/Hydrawise%20REST%20API%20Ver%201.6_0.pdf).
Der Status liefert nächste Zonenläufe und Abrufabstand, keinen vollständigen
Export aller nativen Programme und keine Prüfung von nassem Rasen oder Wegen.
Die Frist von 150 Minuten bleibt erhalten; zonenabhängige Verkürzungen werden
mangels belastbarer Sensorik nicht eingeführt.

## Prüfergebnis und nachfolgende Freigabe

Die vollständige lokale Python-Suite und die 108 Appack-Prüfungen sind im
[Liefernachweis](final-preflight-delivery.json) mit Ergebnisdateien gebunden.
Netzwerkzugriffe sind in pytest standardmäßig gesperrt. Die
[sechs aktuellen Browseransichten](../ui-2026-09-09/final-preflight/browser-check.json)
zeigen bei 320, 390 und 1120 Pixeln keine Überläufe oder JavaScript-Fehler.
Der [unabhängige Integrationsreview](final-integration-review.md) umfasst
Kalenderfreigabe, regelmäßigen Bundle-Refresh und den geschützten Einrichtungsweg.
Der dabei gefundene direkte CLI-Zugangsweg wurde entfernt; das Programm verwendet
jetzt ausschließlich den ausdrücklich benannten ADMIN-Endpunkt. Hostauthentifizierung
und reale Bedienrollen bleiben Prüfungen nach der Installation.


Das [Tagesmodell](coordination-simulation.md) ergibt mit der neuen 03:30-Grenze
642 produktive Minuten im Referenzablauf und 702 Minuten bei einfacher Bündelung.
Die vorausschauende Suche liefert denselben Wert. Bei angenommenen 60–180 Minuten
voller Ladezeit liegt der modellierte Gewinn bei 18–60 Minuten; das ist keine
gemessene Leistungsbandbreite. Frühere Ergebnisse mit einem Start um 03:05 gelten nicht für die
neue Betriebsregel. Die [Controller-Replays](coordination-execution-update.md)
sind ein anderer Vergleich; Gewinne werden nicht addiert. Wasserbedarf und
Sicherheits-/Sportpuffer werden nicht gekürzt. Alle quantitativen Gewinne sind
ausdrücklich Modell- oder Replay-Ergebnisse und kein Liveerfolg.

Die [Offline-Freigabeprüfung](../../scripts/check_release_readiness.py) prüft
gebundene Commit-/ZIP-/Manifestbytes, positive benannte Belege und deren Fristen,
CI, Kalender, Bedarf, konkretes Zeitfenster sowie Platz-/Stations-/Scheduler-
Voraussetzungen. Sie erteilt keine Freigabe. Die
[Eingabevorlage](release-approval-input.example.json) enthält bewusst fehlende
Belege und ergibt `NOT_READY`. Es werden keine fiktiven PASS-Nachweise erstellt.
Der neue Installationsvergleich, Parallelbetrieb und Gerätepilot dürfen erst
nach der dafür erforderlichen Aktivierung beurteilt werden. Sie sind keine
Voraussetzung für einen ersten betreuten Test, wohl aber für unbeaufsichtigten Ausbau.

Noch vor der abschließenden Aktivierungsfreigabe fehlen der bestätigte Schutz
von Station und Zufahrt sowie eine benannte Person mit lokaler Stoppmöglichkeit.
Die neuen Trainings- und Uhrzeitregeln sind durch die Nutzeranweisung geklärt.
Ein Ladeereignis erzeugt weiterhin keinen zusätzlichen Wasserbedarf; jeder
verschobene Lauf bleibt an den bereits erforderlichen Originalbedarf gebunden.
Die nicht nachgewiesenen physischen Voraussetzungen werden nicht aus einer
funktionierenden API abgeleitet.

Der konkrete Ablauf und der gerätebewusste Rückfall stehen im
[Freigabeablauf](final-activation-runbook.md). Ein pauschales „jetzt live“ kann
fehlende Tatsachen nicht bestätigen. Der Abschlussbericht darf daher die
Entwicklung als geprüft, aber noch nicht den vollständigen unbeaufsichtigten
Betrieb als abnahmebereit bezeichnen.
