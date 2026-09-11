# Grund für die nächste Stationsfrist

Der Erklärungstext unter der Rückkehrzeit lautete bisher immer „Vor der nächsten
Platzbelegung“. Beim Auswählen der frühesten Sperre wurde deren Quelle verworfen.
Auch eine Bewässerung wurde deshalb als allgemeine Platzbelegung dargestellt.

Die Anzeige behält jetzt die Quelle der frühesten Sperre:

- Bewässerung: **„Danach wird bewässert.“**
- Training, Spiel oder andere Belegung: „Vor der nächsten Platzbelegung.“
- Zusammengefasste Belegung und Bewässerung: „Danach sind Platzbelegung und
  Bewässerung geplant.“

Die Rückkehrzeit, Bedienflächen und Gerätesteuerung bleiben unverändert.
Die Datenquelle `source` stammt aus der bestehenden Belegungsplanung; bei
zusammengefassten Sperren enthält sie mehrere mit `+` verbundene Quellen.

26 gezielte Tests in `tests/test_appack_return_status.js` erfolgreich, darunter
der Fall 04:00 Uhr mit Bewässerung, eine frühere Trainingssperre, ungültige und
vergangene Einträge sowie kombinierte und fehlende Quellen. Keine Gerätebefehle.

Appack-Vorlage: `6a86ab6c4b3c829dd60de9b7` (`Platzpflege.tpl`). Der Vergleich vor
dem Speichern begrenzt die Änderung auf `pfNextMoment`; die Vorlage entspricht
ansonsten dem vorherigen veröffentlichten Stand. Details zur abschließenden
Prüfung stehen in `publication.json`. Rückfall: dieselbe Funktion aus Commit
`c56cdc3` wiederherstellen und beide Templatekopien synchronisieren.
