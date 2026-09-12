# Warum „Alle Zonen starten“ fehlt – 12.09.2026

Die aktuellen [Betriebsdaten](recent-cycles.json), lesend um 12:48 Uhr Berlin abgerufen, belegen gleichzeitig:

- Mäher `STOPPED`, verbunden, Aktivität `NOT_APPLICABLE`. Das ist keine bestätigte Stationsposition.
- Alle sieben Bewässerungszonen aktuell frei, keine unmittelbar bevorstehende Bewässerung.
- Physische Trockenfrist bereits am 11.09.2026 abgelaufen, Hydrawise-Freigabe aktuell erlaubt.
- Steuerungsablauf weiterhin `COMPLETE_HOLD`, keine ausstehende Geräteaktion.

Beide ersten Startblocker sind real: Die Anzeige lässt bei physischem STOP keinen neuen Bewässerungsstart zu; außerdem lehnt die vorhandene Backendannahme einen neuen Lauf bei jeder noch gesetzten Bewässerungsphase ab (`platzwart_console.py`, `IRRIGATION_ALREADY_ACTIVE`). Es liegt nicht am manuellen Zeitfenster und nicht an einer neuen Trockenfrist.

„Alle Zonen starten“ ist eine direkte Geräteaktion und wird ausgeblendet, wenn sie nicht zulässig ist. „Einzelne Zonen“ ist Navigation zu den Zoneninformationen und bleibt sichtbar; dort werden Startaktionen ebenfalls nach denselben Voraussetzungen ausgeblendet. Die fehlende Erklärung dieses Unterschieds war ein UI-Mangel.

## Änderung

Die Bewässerungskarte und die Zonenunterseite zeigen jetzt bei einer fehlenden Startmöglichkeit den passenden kurzen Hinweis, z.B.:

> Manueller Start nicht möglich: Der Mäher ist gestoppt. Bitte vor Ort freigeben und sicher parken.

Nach der Freigabe verschwindet der Hinweis, sobald die Startvoraussetzungen erfüllt sind. Wenn allein der bisherige Ablauf weiter sperrt, wird dies ausdrücklich genannt. Keine geänderten Startberechtigungen, Zeitfenster, Trockenfristen oder Geräteaktionen.

## Noch offene Backendfrage

`COMPLETE_HOLD` wird unter anderem bei einem erfolgreichen Mäherstart (`mower/full_failsafe.py`, nach `remember_accepted_intent`) oder bei der Vorbereitung eines neuen fälligen Hydrawise-Plans bereinigt. Ein manueller neuer Wasserlauf wird in `platzwart_console.py` davor wegen der noch gesetzten Phase abgelehnt. Die UI darf deshalb nicht versprechen, dass allein die physische STOP-Freigabe den Wasserstart wieder verfügbar macht.

Nächster Entwicklungsschritt: abgeschlossenen Bewässerungsablauf von einer neuen manuellen Bewässerungsanforderung entkoppeln, unter Erhalt von Gerätejournal, Idempotenz, bestätigter Stationsposition und persistenter Trockenfrist. Dafür sind Backendänderungen und gezielte Tests erforderlich. Diese Erklärungskorrektur setzt keinen Produktionszustand zurück.

## Prüfung und Auslieferung

- [218 Appack-Tests bestanden](tests.txt), einschließlich der bestehenden 19.008 Kombinationen. Neue Fälle prüfen die konkret gemeldete Kombination, Pflicht zur Erklärung, fehlende Daten, einzelne Zonenrechte, laufende Anfragen und das Entfernen alter Hinweise nach Freigabe.
- Designquelle, HTML und Textkopie synchron.
- Vollständige bisherige CMS-Vorlage mit Ausgangscommit `987101f4ad3bbdf95dab5a95f5d8bc2973472563` verglichen; vollständiger neuer Editorinhalt vor dem Speichern mit der getesteten Vorlage verglichen.
- [Öffentliche Auslieferung](publication.json) am 12.09.2026 um 12:53:53 Uhr Berlin bestätigt. Keine neue native Telefonabnahme. App vollständig schließen und neu öffnen, um die Vorlage zu laden.
- Rückfall: Vorlage aus Ausgangscommit wieder einsetzen. Das setzt keine Geräteaktion zurück und gibt keine Startsperre frei.
