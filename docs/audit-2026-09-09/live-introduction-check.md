# Startprüfung am 9. September 2026

**Historische Startaufnahme.** Die anschließend erteilte Freigabe, erfolgreiche
Sicherung und entfernte Leseberechtigung sind im
[aktuellen Ausführungsstand](live-introduction-progress.md) dokumentiert.

**Stand: 17:55 Uhr Europe/Berlin. Die Startprüfung läuft; es wurde noch kein
Deployment und kein Gerätebefehl ausgeführt.** Die Aussage des Nutzers, nun
beginnen zu können, wird als Auftrag zur betreuten Einführung behandelt. Sie
ersetzt weder einen freien Platz noch den Nachweis einer funktionierenden
Steuerung. Während des vom Nutzer gemeldeten Trainings bleibt eine Geräteprobe
auf dem Platz ausgeschlossen.

## Neue Angaben und aktuelle Nachweise

| Prüfung | Ergebnis | Nachweis und Grenze |
|---|---|---|
| Station und Zufahrt | Laut Nutzer außerhalb des Beregnungsbereichs; nur der eigentliche Platz wird bewässert | Aktuelle ausdrückliche Nutzerangabe. Die frühere offene Ortsfrage ist damit beantwortet; keine eigene Ortsbesichtigung behauptet. |
| Training und Aufsicht | Training läuft laut Nutzer. Benannte Aufsicht, tatsächliches Trainingsende und anschließende Platzfreigabe fehlen noch | Rückfrage gestellt; keine Endzeit aus einer nicht verfügbaren Belegungsantwort erfunden. |
| Mäher, Abruf 17:41:50 | Eine Maschine, Station, 100 % Akku, verbunden, kein Gerätefehler; leerer nativer Kalender | Herstellerantwort; Statuszeit 17:39:38, damit damals innerhalb von 180 Sekunden. Override `NOT_ACTIVE` ist kein nachgewiesenes unbegrenztes Parken. |
| Mäher, Abruf 17:53:47 | Weiterhin Station, 100 %, verbunden, kein Gerätefehler; leerer nativer Kalender | Enthaltene Statuszeit 17:42:27, also rund 680 Sekunden alt. Frische HTTP-Antwort und frischer Gerätezustand sind unterschiedliche Nachweise. Keine Startfreigabe aus diesem Abruf. |
| Bewässerung, Abruf 17:41:50 | Alle sieben erwarteten Zonen vorhanden, keine Zone aktiv oder unmittelbar bevorstehend | Frischer Hydrawise-Status. Nächster gemeldeter vollständiger Plan am 10.09. von 04:30 bis 07:10 Uhr, 160 Minuten, keine nominellen Pausen. Keine tatsächliche Ausführung behauptet; erneute Prüfung vor jedem Eingriff erforderlich. |
| Aktuelle Platzbelegung | GET für 09.–10.09., Sommerplan, liefert HTTP 500 | Der Platz wird deshalb nicht als frei gewertet. Die bekannte öffentliche Fehlermeldung wird nicht mit einer leeren Belegung verwechselt. |
| Laufende Steuerung | Fehler weiterhin nachgewiesen, zuletzt im hier geprüften Log 17:52:30 | Aktuelles und vorheriges Laufzeitmanifest sind älter als erlaubt. Die alte Software bricht vor dem vollständigen Steuerzyklus ab. Die entwickelte Schutzbehandlung ist noch nicht installiert. |
| Parallele GitHub-Läufe | Keine Läufe in `in_progress`, `queued` oder `waiting` bei der Abfrage | Zeitgebundene GitHub-Aufnahme; schließt spätere Dispatches oder unabhängige Gerätesender nicht aus. |
| Geprüfter Quellstand | `204078c63ccc55b10d5c1715b4d12a6c22a7acf9` | Beide PR-CI-Läufe erneut erfolgreich gelesen; keine erfolgte Installation daraus abgeleitet. |

Die Gerätepläne, Einstellungen und Rohantworten liegen ausschließlich im
privaten Diagnoseordner außerhalb des Repositorys. Der erneute Einstellungs-
vergleich bestätigt sämtliche Steuerungswerte. Beim Mail-Absendernamen weicht
die lokale Textdecodierung ab; für die weitere Sicherung wird die erneut korrekt
decodierte Antwort verwendet. Es wurde keine Appsetting-Änderung gesendet.

## Konkrete Zugriffslücke bei der Rückfallsicherung

Der Azure-Zugang erhält bei der Table-Abfrage HTTP 403 mit
`AuthorizationPermissionMismatch`. Öffentlicher Netzwerkzugriff ist erlaubt;
es handelt sich nicht um einen belegten Netzwerkblock. Shared-Key-Zugriff ist
deaktiviert und wurde nicht eingeschaltet.

Das unabhängige Review der tatsächlich installierten 46 Quelldateien bestätigt:
Der authentifizierte Anzeigenzugang `/api/platzwart/status` liefert nur einen
Ausschnitt des Zustands, nicht die vollständigen Ablauf-, Besitz- und
Befehlsdaten. Er ersetzt keine wiederherstellbare Sicherung. Auch das
Bewässerungsjournal wird nur für Anzeigen aufbereitet; ein vollständiger Export
ist über die installierten HTTP-Funktionen nicht vorhanden.

Vorbereitet ist deshalb eine zusätzliche, ausschließlich lesende Berechtigung
`Storage Table Data Reader` für das angemeldete Konto, begrenzt auf die bestehende
Tabelle `MowerAutomationState`. Kontokennung, Rollenkennung und genauer
Ressourcenumfang wurden lesend ermittelt und privat abgelegt. Die erforderliche
Freigabe wurde angefragt. **Es wurde keine Rolle zugewiesen.** Eine neue
Berechtigung darf nur nach dieser Freigabe eingerichtet und nach der Sicherung
wieder entfernt werden; bereits bestehende Berechtigungen bleiben erhalten.

Die Sicherung muss die vollständige Entity `ssv53-mower-control` in der
Partition `ssv53-mower`, die relevanten rohen Bewässerungsjournale und ihre
Versionen umfassen. Ein Teil der Felder aus einem Zykluslog genügt nicht.
Ein Fehler oder eine unvollständige Abfrage beendet den Sicherungsversuch ohne
fingierten Erfolg. Solange diese Rückfallsicherung fehlt, erfolgt kein Austausch
der Steuerung.

## Herstellerprüfung zum Alter der Mähermeldung

Das unabhängige Review und ein eigener Abruf der aktuellen Primärquellen
bestätigen: `metadata.statusTimestamp` bezeichnet die letzte Statusaktualisierung
im Husqvarna-Backend. Es ist weder die Abrufzeit noch ein direkt im Mäher erzeugter
Sensorzeitstempel. `connected=true` beantwortet eine andere Frage und frischt
die Stationsmeldung nicht auf.
[Offizielles API-Schema](https://docs.developer.husqvarnagroup.cloud/automower-connect-api/swagger.yml)

Husqvarna beschreibt außerdem einen Timeout im Mäher und dadurch mögliche
Ereignisabstände von 15 Minuten. Das passt zur beobachteten längeren Meldungspause;
es beweist nicht deren konkrete Ursache oder eine unabhängige aktuelle
Stationsposition. Die Herstellerangabe zum Ereignisabstand ist keine Garantie
für die Frische beliebiger REST-Antworten.
[Offizielle Ereignisdokumentation](https://docs.developer.husqvarnagroup.cloud/automower-connect-api/WebSocket.md)

Die bestehende 180-Sekunden-Grenze bleibt erhalten. Daraus folgt eine offene
Verfügbarkeitsfrage für den Pilot: Nach dem Parken müssen genügend neue
Statusmeldungen für die erforderlichen Stationsbestätigungen eintreffen.
Andernfalls wartet die Steuerung, auch wenn das Gerät laut letzter Meldung
bereits in der Station steht. Es wäre unbelegt, solche Wartezeit allein durch
eine höhere Altersgrenze für sicher vermeidbar zu erklären. Vor einer
unbeaufsichtigten Freigabe sind mindestens ein vollständiger Lade-/Parkverlauf
und die Freigabepunkte jeder Beregnungszone mit tatsächlichen Meldungszeiten
zu beobachten. Regelmäßiges erneutes HTTP-Abfragen ist dafür kein Ersatz.

## Auslieferungsobjekt und nächste Schritte

Das aus CI heruntergeladene FULL_FAILSAFE-Quellarchiv wurde erneut gehasht:

- SHA-256: `12c7484f8618f1d68efa03ee6e509f4438fd4141af0168bf5a0e71370e54fc71`.
- Umfang: 61 Quelldateien plus Manifest.
- [Codeprüfung](https://github.com/Rohdeo87/ssv53-heimspiele/actions/runs/34370825977): erfolgreich.
- [Paketprüfung](https://github.com/Rohdeo87/ssv53-heimspiele/actions/runs/34370825899): erfolgreich.
- [Entwicklungs-PR 47](https://github.com/Rohdeo87/ssv53-heimspiele/pull/47): noch nicht zusammengeführt.

Nach der Freigabe der lesenden Sicherung wird der
[Einführungsablauf](final-activation-runbook.md) fortgesetzt: gesicherte Geräte,
vollständige Rückfallobjekte, geschlossene Schreibgates, exakt geprüftes
Quellpaket mit Azure-Remote-Build und anschließend Installationsprüfung.
Microsoft verlangt für Python im Flex-Plan einen Remote-Build; ein lokales
Quellarchiv allein ist kein installiertes Laufzeitpaket.
[Offizielle Deploymentanleitung](https://learn.microsoft.com/en-us/azure/azure-functions/flex-consumption-how-to#deploy-your-code-project)

Erst der installierte kompatible Verbraucher erlaubt die anschließende
kontrollierte Wiederherstellung der Import-/Bundlekette. Ein isoliertes
Aktualisieren des Bundles bei weiterhin aktiven alten Schreibgates könnte die
alte Steuerung unerwartet wieder anlaufen lassen und wird deshalb nicht als
Abkürzung verwendet.

Die App-Veröffentlichung, gemeinsame Kalenderaktivierung, befehlsfreie
Beobachtung und spätere begrenzte Geräteprobe folgen den vorhandenen Stufen.
Bestätigte Station/Zufahrt allein schließen diese Nachweise nicht ab.
Insbesondere wird die ursprüngliche Bewässerung nicht zusätzlich ausgelöst,
um die Einführung zu beschleunigen.

## Ergebnis dieser Prüfung

**Analysiert und erneut gelesen:** Nutzerangabe zum Beregnungsbereich,
Herstellerzustände, nächster Wasserplan, Belegungsfehler, Steuerungsausfall,
Zugriffsgrenze, CI-Status und Paketbytes.

**Vorbereitet:** minimaler lesender Zugriff für die noch fehlende
Zustandssicherung; unverändertes geprüftes Auslieferungsobjekt.

**Nicht ausgeführt:** Rechtevergabe, Deployment, PR-Merge, App-Veröffentlichung,
Kalenderinitialisierung, Änderung von Geräteplänen oder Schreibgates,
Gerätebefehle und Livepilot. Diese Prüfung ist kein sicherer Livebetriebsnachweis.
