# Changelog

## Version 12.3

- Mobiler Änderungsbericht in der GitHub-Actions-Summary.
- Veröffentlichungsschutz bei leerem Folgefeed oder massivem Spielverlust.
- Sichere, standardmäßig nicht veröffentlichende Branch-Testläufe.
- GitHub-Issue-Benachrichtigungen bei Änderungen, Blockierungen und Fehlern.
- Deduplizierung identischer Warnungen und automatische Erholung technischer Warnungen.
- Separater Workflow für echte Testbenachrichtigungen.
- Erweiterte Tests für Änderungsbericht und Benachrichtigungslogik.

## Version 12.2

- Reine Anstoß-Konflikte bei derselben Spiel-ID werden über die offizielle Spiel-Detailseite aufgelöst.
- Terminverschiebungen können dadurch sicher den alten, noch zwischengespeicherten Termin ersetzen.
- Die Datumserkennung übernimmt keine Werte mehr aus benachbarten Spielblöcken.
- Öffentliche Rohantworten werden im Diagnose-Artefakt unter `raw/` mitgespeichert.
- Widersprüche bei Spielnummer, Mannschaften, Spielstätte oder Status bleiben harte Qualitätsfehler.
- Abruflimit, Schutzlogik, Platzregeln und 60-Minuten-Puffer bleiben unverändert.

## Version 12.1

- Identische oder nur ergänzende Mehrfachdarstellungen derselben Spiel-ID werden sicher zusammengeführt.
- Widersprüchliche Mehrfachdarstellungen bleiben ein harter Qualitätsfehler.
- Die Diagnose nennt zusammengeführte IDs und Konfliktfelder getrennt.
- Abruflimit, Schutzlogik, Platzregeln und die 60-Minuten-Puffer bleiben unverändert.

## Version 12

- Feste Mannschaftsliste durch den vereinsweiten FUSSBALL.DE-Spielplan ersetzt.
- Neue Vereinsmannschaften und deren Spiele werden automatisch erkannt.
- Vereinsspielplan wird in vier anfänglichen Quartalsfenstern abgerufen.
- Betroffene Zeitfenster werden bei `Mehr laden` oder erreichter Antwortgrenze automatisch geteilt.
- Harte Obergrenze von zehn Requests bleibt bestehen.
- Lückenlose Saisonabdeckung wird vor der Veröffentlichung geprüft.
- Persistentes Mannschaftsregister `state/team_registry.json` ergänzt.
- Neue, bekannte und im Lauf nicht gesehene Mannschaften werden protokolliert.
- Belegungspuffer auf 60 Minuten vor dem Anstoß und 60 Minuten nach dem Spiel gesetzt.
- Konservative Standardspieldauer bleibt zunächst 90 Minuten; Dauerregeln sind konfigurierbar.
- Feed enthält zusätzlich `teamCategory`.
- Bestehende Server-, Zeitfenster-, Sicherheits- und Konfliktschutzmaßnahmen bleiben aktiv.

## Version 11

- Teambezogene Zeitfenster für Herren, Ü40 und Ü50.
- Neun Requests pro Lauf.
- Erfolgreicher produktiver GitHub-Actions-Lauf als bisherige stabile Ausgangsbasis.
