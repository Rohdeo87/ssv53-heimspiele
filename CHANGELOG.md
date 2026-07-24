# Version 7

- paginierten Zusatz `mode/PAGE` aus dem regulären Spielplan-Endpunkt entfernt
- vollständiger Mannschaftsspielplan je Team wird nun in einer Antwort angefordert
- Heimspielfilter `match-type/1` und Spielstättenabfrage `show-venues/true` bleiben erhalten
- Request-Anzahl und alle Sicherheitssperren bleiben unverändert
- Regressionstest gegen eine versehentliche Rückkehr zum paginierten Endpunkt ergänzt

# Version 6

- stabile FUSSBALL.DE-Spiel-ID aus dem letzten `/-/spiel/<ID>`-Segment
- `spielfrei` wird automatisch ausgeschlossen und nicht mehr zur Platzprüfung gemeldet
- Zeitwerte enthalten den Offset für `Europe/Berlin`
- ICS-Ausgabe verwendet `TZID=Europe/Berlin`
- Wettbewerbsname wird ohne vorangestellten Wochentag ausgegeben

# Änderungen in Version 5

- HTTP 403 und 406 lösen jetzt sofort einen globalen Abbruch des gesamten Laufs aus.
- Eine permanente Sicherheitssperre wird in `state/request_state.json` gespeichert.
- Solange die Sperre aktiv ist, erfolgen auch in späteren Läufen keinerlei Netzrequests.
- Typische Cloudflare-, Bot-Schutz-, CAPTCHA- und Challenge-Seiten werden auch bei HTTP 200 erkannt.
- Sicherheitsseiten werden ausdrücklich nicht umgangen und nicht erneut abgerufen.
- Der letzte erfolgreiche Feed bleibt während der Sperre unverändert.
- Manuelle Entsperranleitung in der README ergänzt.
- Automatisierte Tests für 403, 406, HTTP-200-Challenge und die requestfreie Folgesperre ergänzt.

# Änderungen in Version 4

- Automatische Abrufe auf das Berliner Zeitfenster 06:00–22:00 Uhr begrenzt.
- Vier sichere UTC-Startfenster ergänzt, die bei Sommer- und Winterzeit innerhalb des erlaubten Fensters liegen.
- Zufällige Startverschiebung von 2 bis 12 Minuten vor jedem Abruf eingebaut.
- Zweite Zeitfensterprüfung unmittelbar vor dem ersten Netzabruf ergänzt.
- Stark verspätete GitHub-Läufe werden ohne Anfrage an FUSSBALL.DE beendet.
- Konfigurierbare Zeitfensterregeln unter `schedule_protection` ergänzt.
- Automatisierte Tests für Zeitfenster, Zufallsverzögerung und späten Abbruch ergänzt.

# Änderungen in Version 3

- Aktualisierungsintervall von zwei auf sechs Stunden reduziert.
- Regulärer Betrieb auf einen einzigen Spielplan-Endpunkt begrenzt.
- Mindestabstand von drei Sekunden plus Zufallsabstand erzwungen.
- Harte Obergrenze von zehn HTTP-Anfragen je Lauf eingebaut.
- Höchstens ein Retry; nur bei Timeout oder HTTP 502/503/504.
- HTTP 403/404 werden nicht wiederholt.
- HTTP 429 stoppt den gesamten Lauf sofort und speichert `Retry-After` persistent.
- Fehlerhafte oder unvollständige Läufe überschreiben den letzten Feed nicht.
- Persistenter Schutzstatus unter `state/request_state.json` ergänzt.
- Automatisierte Tests für Request-Grenzen, 404, 429 und 503 ergänzt.
