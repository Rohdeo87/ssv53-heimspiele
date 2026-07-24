# SSV53 Heimspiele – geschützter GitHub-PoC

Dieser PoC ruft die konfigurierten Heimspiele zurückhaltend ab, ordnet sie Rasen oder Kunstrasen zu und veröffentlicht die geprüften Ergebnisse in `public/matches.json`.

## Eingebaute Schutzregeln

- Automatische Datenabrufe finden ausschließlich zwischen **06:00 und 22:00 Uhr in der Zeitzone Europe/Berlin** statt.
- Vier vorsichtige Startfenster pro Tag. Die UTC-Cronzeiten sind so gewählt, dass sie sowohl während der Sommer- als auch während der Winterzeit innerhalb des erlaubten Berliner Zeitfensters liegen.
- Vor jedem Abruf wird eine zufällige Wartezeit von **2 bis 12 Minuten** eingefügt. Dadurch verschiebt sich der tatsächliche Abrufzeitpunkt leicht.
- Nach der Wartezeit wird das Berliner Zeitfenster erneut geprüft. Ein verspäteter GitHub-Lauf wird außerhalb des Fensters ohne Netzabruf beendet.
- Regulär genau ein fest konfigurierter Spielplan-Endpunkt je Mannschaft.
- Mindestens drei Sekunden Abstand zwischen zwei HTTP-Anfragen, zusätzlich 0–1 Sekunde Zufallsabstand.
- Höchstens zehn HTTP-Anfragen pro vollständigem Lauf. Diese Grenze ist zusätzlich fest im Programm verankert.
- Höchstens ein Wiederholungsversuch.
- Wiederholung nur bei Timeout oder HTTP 502, 503 beziehungsweise 504.
- HTTP 404 und andere normale Clientfehler werden nicht wiederholt.
- Bei HTTP 403 oder 406 wird der gesamte Lauf sofort beendet und eine **dauerhafte globale Sicherheitssperre** gespeichert.
- Typische Challenge-, Bot-Schutz-, CAPTCHA- und Sicherheitsseiten werden auch bei HTTP 200 erkannt. Sie lösen dieselbe globale Sperre aus; die Seite wird nicht umgangen oder erneut angefragt.
- Solange die globale Sicherheitssperre aktiv ist, beendet sich jeder spätere Lauf **vor dem ersten Netzrequest**. Die Sperre wird ausschließlich nach manueller Prüfung aufgehoben.
- Bei HTTP 429 wird der gesamte Lauf sofort beendet. `Retry-After` wird gespeichert und bis zum Ablauf werden keine neuen Requests ausgeführt.
- Es laufen niemals zwei Aktualisierungen gleichzeitig.
- Bei jedem unvollständigen oder fehlerhaften Lauf bleibt der letzte erfolgreich veröffentlichte Feed unverändert.
- Der User-Agent nennt den SSV53, die Website und eine Kontaktadresse.

Mit den aktuell drei Mannschaften entstehen im Normalbetrieb höchstens zwölf reguläre HTTP-Anfragen pro Tag. Durch verspätete oder übersprungene Läufe kann die tatsächliche Zahl niedriger sein.

## Geplante Abruffenster

GitHub führt Cron-Ausdrücke in UTC aus. Geplant sind vier Kandidaten pro Tag:

- 05:20 UTC
- 10:20 UTC
- 15:20 UTC
- 19:20 UTC

Das entspricht je nach Sommer- oder Winterzeit ungefähr **06:20 bis 21:20 Uhr Berliner Zeit**. Danach kommt jeweils die zufällige Verzögerung von 2 bis 12 Minuten. Falls GitHub einen Lauf so stark verspätet startet, dass 22:00 Uhr nicht mehr sicher eingehalten werden kann, wird kein Request an FUSSBALL.DE gesendet.

## Globale Sicherheitssperre bei 403, 406 oder Challenge

Wird eine serverseitige Sicherheitsreaktion erkannt, schreibt der PoC unter anderem folgende Werte in `state/request_state.json`:

```json
{
  "security_lock": true,
  "security_lock_reason": "HTTP 403 oder erkannte Challenge",
  "manual_unlock_required": true
}
```

Ab diesem Zeitpunkt werden auch bei späteren automatischen oder manuellen Läufen **keine weiteren Requests an FUSSBALL.DE** gesendet. Der letzte erfolgreich veröffentlichte Feed bleibt unverändert.

Die Sperre darf erst nach Prüfung der Ursache und gegebenenfalls Abstimmung mit FUSSBALL.DE/DFB aufgehoben werden. Dazu in GitHub die Datei `state/request_state.json` bearbeiten und ausschließlich diese Felder zurücksetzen:

```json
"security_lock": false,
"security_lock_reason": "",
"security_lock_at": "",
"security_lock_url": "",
"security_lock_http_status": null,
"manual_unlock_required": false
```

Andere Statusfelder bitte nicht verändern. Anschließend kann der Workflow einmal manuell innerhalb des Zeitfensters gestartet werden.

## Einmalige Einrichtung

1. Öffentliches GitHub-Repository `ssv53-heimspiele` anlegen.
2. Den vollständigen Inhalt dieses Ordners hochladen – einschließlich `.github` und `state`.
3. Im Repository `Actions` öffnen und Workflows freigeben, falls GitHub danach fragt.
4. Workflow **SSV53 Heimspiele aktualisieren** öffnen.
5. Zwischen 06:00 und 22:00 Uhr auf **Run workflow** und anschließend erneut **Run workflow** klicken.
6. Warten, bis der Lauf grün ist.

Danach ist der Feed normalerweise unter folgender Adresse erreichbar:

`https://raw.githubusercontent.com/DEIN-GITHUB-NAME/ssv53-heimspiele/main/public/matches.json`

`DEIN-GITHUB-NAME` muss durch den eigenen GitHub-Benutzernamen ersetzt werden.

## Wichtige Dateien

- `config.json`: Mannschaften, Saisonzeitraum, Platzzuordnung, Zeitfenster und konservative Request-Einstellungen
- `schedule_guard.py`: prüft das Berliner Abruffenster und erzeugt die zufällige Startverschiebung
- `state/request_state.json`: persistenter Schutzstatus für `Retry-After` sowie die globale 403/406/Challenge-Sperre
- `public/matches.json`: Feed für den Appack-Belegungsplan
- `public/appack_preview.csv`: gut lesbare Kontrollliste
- `public/review_matches.json`: Spiele mit ungeklärter Spielstätte
- `public/rasen.ics` und `public/kunstrasen.ics`: optionale iCal-Feeds

## Diagnosemodus

Alternative URL-Varianten werden im automatischen Workflow ausdrücklich **nicht** getestet. Nur für eine manuelle technische Diagnose kann lokal folgender Befehl verwendet werden:

```bash
python poc_scraper.py --config config.json --output generated --state state/request_state.json --diagnostic-endpoints --verbose
```

Auch im Diagnosemodus bleibt die harte Obergrenze von zehn Requests bestehen.


## Datenqualitätskorrekturen in Version 6

Die technische Spiel-ID wird aus dem stabilen letzten `/-/spiel/<ID>`-Segment der FUSSBALL.DE-URL übernommen. Einträge mit dem Gegner oder Heimteam `spielfrei` werden automatisch ausgeschlossen. Datumswerte enthalten den Berliner UTC-Offset.


## Vollständigkeitskorrektur in Version 7

Der reguläre Abruf verwendet jetzt den vollständigen, nicht paginierten Mannschaftsspielplan-Endpunkt ohne `mode/PAGE`:

```text
/ajax.team.matchplan/-/mime-type/HTML/show-venues/true/match-type/1/...
```

`match-type/1` begrenzt weiterhin ausschließlich auf Heimspiele. Die Spielstätten bleiben über `show-venues/true` enthalten. Durch das Entfernen von `mode/PAGE` werden alle im gewählten Saisonzeitraum veröffentlichten Heimspiele einer Mannschaft in derselben Antwort verarbeitet. Die Anzahl der Requests bleibt unverändert bei genau einem Spielplanabruf je konfigurierter Mannschaft.
