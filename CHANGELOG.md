# Changelog

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
