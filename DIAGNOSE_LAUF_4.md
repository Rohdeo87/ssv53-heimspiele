# Diagnose des GitHub-Laufs 4

## Befund

Der Parser hat die drei gelieferten Antworten vollständig verarbeitet:

- Herren: 10 Zeilen, 10 technische Spiel-IDs
- Herren Ü40: 8 Zeilen, 7 Spiel-IDs plus ein Eintrag „spielfrei“
- Herren Ü50: 7 Zeilen, 6 Spiel-IDs plus ein Eintrag „spielfrei“
- keine fehlenden Spiel-IDs
- keine doppelten Spiel-IDs
- keine ungeklärten Spielstätten

Damit ist der blockbasierte Parser nicht die Ursache für die Zahl 16. Die Quelle wurde mit `match-type/1` abgerufen und enthielt damit ausschließlich formal als Heimspiele geführte Begegnungen.

## Korrektur in Version 9

Version 9 lädt den vollständigen Mannschaftsspielplan ohne `match-type/1`. Danach entscheidet ausschließlich die Spielstätte über die Aufnahme:

- Platz 1 → Rasen
- Platz 2/Kunstrasen/KR → Kunstrasen
- Perwenitz/Paaren → ausgeschlossen
- unbekannt → Qualitätsfehler

Damit werden auch Spiele berücksichtigt, bei denen das SSV-Team formal als Gast geführt wird, das Spiel aber tatsächlich in Schönwalde auf Platz 1 oder Platz 2 stattfindet.

## GitHub-Konflikt

Der Fehler `fetch first` entstand unabhängig vom FUSSBALL.DE-Abruf. Während der Workflow lief, hatte sich der Branch `main` verändert. Version 9 holt vor dem Speichern den neuesten Stand und kopiert anschließend nur die erzeugten Daten beziehungsweise den Schutzstatus zurück. Bei einem fehlgeschlagenen Qualitätscheck wird `public` nicht überschrieben.
