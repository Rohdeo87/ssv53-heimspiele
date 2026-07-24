# Changelog

## Version 9

- Auswahl vollständig auf den tatsächlichen Austragungsort umgestellt.
- `match-type/1` entfernt; alle Mannschaftsspiele werden geladen.
- Formal als Gast geführte Spiele auf Platz 1/2 werden jetzt berücksichtigt.
- Teamrolle `home`, `away` oder `unknown` wird protokolliert.
- Feed- und ICS-Titel von „Heimspiel“ auf „Spiel“ umgestellt.
- GitHub-Push gegen parallele Nutzeränderungen konfliktfest gemacht.
- veralteten `datetime.utcnow()`-Aufruf ersetzt.
- zusätzliche Tests für Endpunkt und formales Auswärtsspiel auf Platz 1.

## Version 8

- Blockbasierter Parser und Mindestprüfung für 17 Rasen-Spiele.
