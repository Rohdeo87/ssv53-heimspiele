# SSV53 – FUSSBALL.DE-Platzbelegung PoC Version 9

Diese Version behebt zwei Probleme aus dem Diagnoselauf 4:

1. Der Abruf lädt nun **alle Spiele der drei Mannschaften** statt nur die formal als Heimspiele geführten Begegnungen. Entscheidend für den Belegungsplan ist ausschließlich die veröffentlichte Spielstätte. Ein Spiel wird deshalb auch dann übernommen, wenn das SSV-Team in DFBnet formal als Gast geführt wird, das Spiel aber auf dem **Sportplatz Schönwalde Strandbad, Platz 1 oder Platz 2** angesetzt ist.
2. Der GitHub-Workflow speichert Ergebnisse jetzt konfliktfrei, auch wenn während der zufälligen Wartezeit neuer Code in `main` hochgeladen wurde.

## Warum diese Änderung nötig ist

Der bisherige Endpunkt enthielt `match-type/1`. Dadurch wurden nur formal als Heimspiele geführte Begegnungen geladen. Für eine Platzbelegung ist diese Sicht zu eng: Maßgeblich ist der tatsächliche Austragungsort. Version 9 entfernt den Filter, lädt den vollständigen Mannschaftsspielplan und filtert danach streng nach Spielstätte.

Die Zahl der Requests bleibt unverändert: ein Spielplanabruf je Mannschaft, derzeit also drei Requests pro Lauf. Die Antworten sind lediglich etwas größer. Alle bisherigen Schutzregeln bleiben aktiv.

## Auswahlregeln

- Schönwalde Strandbad, Platz 1 → `Rasen`
- Schönwalde Strandbad, Platz 2 / Kunstrasen / KR → `Kunstrasen`
- Perwenitz → ausgeschlossen
- Paaren → ausgeschlossen
- unbekannte Spielstätte → keine Veröffentlichung, Diagnose erforderlich
- spielfrei → ausgeschlossen

## Installation

1. ZIP entpacken.
2. Den gesamten Inhalt in den lokalen Ordner `ssv53-heimspiele` kopieren und vorhandene Dateien ersetzen.
3. GitHub Desktop öffnen.
4. Summary: `Platzbasierter Abruf Version 9`
5. **Commit to main**.
6. **Push origin**.
7. Auf GitHub unter **Actions → SSV53 Heimspiele aktualisieren → Run workflow** einmal manuell starten.

## Prüfung

Nach einem grünen Lauf `public/summary.json` öffnen. Erwartet werden:

- `selection_mode`: `venue`
- `by_calendar.Rasen`: mindestens 17
- `included_by_formal_role.away`: zeigt, wie viele Spiele trotz formaler Gastrolle wegen des tatsächlichen Platzes aufgenommen wurden
- `review`: 0

## Schutzmaßnahmen

- Abrufe nur zwischen 06:00 und 22:00 Uhr Europe/Berlin
- zufällige Verzögerung 2–12 Minuten
- mindestens 3 Sekunden Abstand
- maximal 10 Requests je Lauf
- maximal ein Retry nur bei 502/503/504 oder Timeout
- globale dauerhafte Sperre bei 403, 406 oder Challenge-Seite
- Sperre bei 429 gemäß `Retry-After`
- keine parallelen Läufe
- bei Qualitätsfehlern bleibt der letzte veröffentlichte Feed unverändert

## Hinweis zur Mindestzahl 17

Die Mindestzahl 17 ist für die aktuelle PoC-Abnahme bewusst noch aktiv. Nach dem ersten bestätigten vollständigen Lauf sollte sie durch eine dynamische Änderungsprüfung ersetzt werden, damit eine spätere legitime Verlegung oder Absetzung den Feed nicht dauerhaft blockiert.
