# SSV53 – FUSSBALL.DE-Platzbelegung PoC Version 12.1

Version 12.1 basiert auf Version 12 und löst die feste Mannschaftsliste aus Version 11 ab. Der Abruf erfolgt jetzt über den vereinsweiten Spielplan des Schönwalder SV 53. Dadurch werden auch Spiele von später neu hinzukommenden Vereinsmannschaften automatisch berücksichtigt, ohne dass ihre Mannschafts-ID vorher in `config.json` eingetragen werden muss.

## Was Version 12.1 automatisch erkennt

Aus jeder Spielzeile werden – soweit FUSSBALL.DE die Angaben bereitstellt – insbesondere gelesen:

- Vereinsmannschaft und Mannschaftsart
- Mannschafts-ID
- Heim- und Gastmannschaft
- formale Heim-/Gastrolle
- Datum und Anstoßzeit
- Wettbewerb, Spielart und Spielnummer
- technische Spiel-ID und Detail-Link
- tatsächliche Spielstätte

Entscheidend für den Belegungsplan bleibt ausschließlich die Spielstätte. Ein formal als Auswärtsspiel geführtes Spiel wird aufgenommen, wenn es tatsächlich auf einem Schönwalder Platz stattfindet.

## Platzlogik

- `Sportplatz Schönwalde Strandbad, Platz 1` → `Rasen`
- `Sportplatz Schönwalde Strandbad, Platz 2`, `Kunstrasen` oder `KR` → `Kunstrasen`
- `Sportplatz Perwenitz`, `Sportplatz Paaren` und eindeutig fremde Spielstätten → ausgeschlossen
- lokale Schönwalder Spielstätte ohne eindeutige Platznummer → Prüfung
- fehlende Spielstätte → Prüfung
- `spielfrei` → ausgeschlossen

Der Feed wird nicht veröffentlicht, solange ein Spiel noch eine manuelle Platzprüfung benötigt.

## Zeiträume der Belegung

Der gewünschte Puffer beträgt nun:

- **60 Minuten vor dem Anstoß**
- **60 Minuten nach dem Spiel**

Da FUSSBALL.DE keine verlässliche Endzeit liefert, setzt Version 12.1 zunächst eine konservative Standardspieldauer von 90 Minuten an. Ein Spiel mit Anstoß um 10:00 Uhr blockiert den Platz daher von 09:00 bis 12:30 Uhr.

Die Standardspieldauer kann später in `event_timing.duration_rules` für bestimmte Mannschaftsarten angepasst werden. Neue Mannschaften bleiben bis zu einer solchen Anpassung mit 90 Minuten sicher abgedeckt.

## Vereinsweiter und skalierbarer Abruf

Version 12.1 verwendet `ajax.club.matchplan` statt einzelner Mannschaftsabrufe. Die Saison wird zunächst in vier Quartalsfenster aufgeteilt. Jede Antwort fordert bis zu 50 Zeilen an.

Falls FUSSBALL.DE trotzdem eine gekürzte Antwort oder einen sichtbaren Hinweis `Mehr laden` liefert, wird nur das betroffene Zeitfenster automatisch halbiert. Dabei gelten weiterhin:

- maximal 10 Requests pro Lauf
- mindestens 3 Sekunden Abstand zwischen Requests
- maximal ein Retry nur bei Timeout oder HTTP 502/503/504
- sofortiger Abbruch bei HTTP 429
- dauerhafte Sicherheitssperre bei HTTP 403, 406 oder erkannter Challenge-Seite
- keine parallelen Läufe
- letzter erfolgreicher Feed bleibt bei Fehlern unverändert

Kann die Saison innerhalb von 10 Requests nicht vollständig und lückenlos erfasst werden, wird nichts veröffentlicht.

## Mannschaftsregister

Erkannte Mannschaften werden in `state/team_registry.json` gespeichert. Die Diagnose und `public/summary.json` weisen aus:

- neu erkannte Mannschaften
- bereits bekannte Mannschaften
- früher bekannte Mannschaften, die im aktuellen Lauf kein Spiel hatten

Das Fehlen einer Mannschaft in einem einzelnen Lauf wird nicht automatisch als Abmeldung interpretiert.

## Qualitätsprüfung

Eine Veröffentlichung erfolgt nur, wenn:

- die akzeptierten Zeitfenster den gesamten Saisonzeitraum lückenlos abdecken,
- keine akzeptierte Antwort möglicherweise gekürzt ist,
- alle vorhandenen Spiel-Links verarbeitet wurden,
- keine Spiel-ID innerhalb einer Antwort doppelt ist,
- alle aufzunehmenden Spiele vollständig sind,
- keine lokale Spielstätte ungeklärt bleibt.

Es gibt weiterhin keine starre Mindestzahl an Spielen.

## Installation

1. ZIP vollständig entpacken.
2. Den vollständigen Inhalt des Ordners `ssv53-heimspiele` in das lokale Repository `ssv53-heimspiele` kopieren. Dieses Wiederherstellungspaket enthält auch `.github`, `state`, `public`, Tests und sämtliche Programmdateien.
3. Vorhandene Dateien ersetzen. Bei Konflikten in `state/request_state.json` oder `state/team_registry.json` grundsätzlich den aktuelleren Stand aus `main/origin` behalten.
4. Bei einem Konflikt in `state/request_state.json` die Version `from main/origin` behalten.
5. Bei Konflikten in Version-12-Dateien wie `poc_scraper.py`, `config.json` oder der Workflow-Datei die lokale Version `from main` behalten.
6. Commit-Text: `Dublettenprüfung Version 12.1`
7. `Push origin` ausführen.
8. Unter GitHub Actions den Workflow `SSV53 Heimspiele aktualisieren` starten.

## Kontrolle nach dem ersten Lauf

In `public/summary.json` sollten insbesondere stehen:

- `publishable: true`
- `review: 0`
- plausible Werte unter `by_calendar`
- alle erkannten Mannschaften unter `teams_discovered`
- höchstens 10 unter `request_count`
- lückenlose Einträge unter `accepted_windows`

Das Diagnose-Artefakt enthält zusätzlich `team_registry.json`, `quality_report.json`, alle gelesenen Spiele und die CSV-Vorschau.
