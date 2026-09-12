# Manuellen Stopp ruhig und verständlich anzeigen

## Befund zur Drei-Minuten-Grenze

Ein neun Minuten alter Zeitstempel allein beweist weder einen Ausfall noch
einen aktuell bestätigten physischen Zustand. Die Grenze von 180 Sekunden
ist eine Regel unserer Software (`_mower_telemetry_fresh` und Controllerprüfung
in `platzwart_console.py`), keine dokumentierte Lebensdauer eines STOP-Zustands.
Das eingelesene Husqvarna-Metadatenmodell unterscheidet `connected` und
`statusTimestamp`. Letzterer bezeichnet das letzte Statusupdate und wird laut
Herstellerschema im Backend erzeugt. Ein erfolgreicher HTTP-Abruf setzt diesen
Zeitstempel nicht automatisch auf die aktuelle Zeit.

Die [beobachteten Kontrollzyklen](observations.json) liefern weiter verbunden,
STOPPED, keine laufende Bewässerung, abgelaufene Trockenfrist und keine
Gerätebefehle. Der physische STOP war vom Nutzer berichtet worden.
Die Drei-Minuten-Grenze führte bei diesem unveränderten Stopp lediglich zu
einer lauteren Anzeige, ohne eine andere sinnvolle Handlung zu ermöglichen.

## Zustände getrennt behandeln

| Situation | Anzeige / Bedeutung | Freigabe |
| --- | --- | --- |
| Unveränderter STOP bei funktionierender Datenverarbeitung | „Manuell gestoppt“. „Zum Fortsetzen bitte den Mäher vor Ort freigeben.“ | STOP bleibt wirksam; kein Start und kein Stationsnachweis daraus. |
| Gerät nicht verbunden, Steuerung ausgefallen oder Belegungsdaten ungültig | Passende bestehende Fehler- und Handlungsmeldung hat Vorrang. | Bisherige Sperren bleiben. |
| Fahren, Heimfahrt, Laden oder Befehlsbestätigung | Zeitkritische Zustandsdaten; Alter darf nicht pauschal ignoriert werden. | Bestehende Aktualitätsprüfungen unverändert. |
| Gehaltene bestätigte Station | Bereits vorhandener, separat gebundener Parknachweis mit laufender Revalidierung. | Keine Ausweitung durch diesen Anzeigefix. |
| Laufende oder unbekannte Bewässerung, unbestätigter Start | Bestehende Konfliktmeldung hat Vorrang. | Keine Ausnahme durch den STOP-Anzeigetext. |

Die richtige Änderung ist damit eine zustandsabhängige Auswertung, keine
globale Verlängerung auf beispielsweise 15 oder 30 Minuten. Eine spätere
Überarbeitung der allgemeinen Aktualitätsregeln müsste Gerätebericht,
letzten erfolgreichen Abruf, Zustand und offene Befehle getrennt nachweisen.
Die bisherige 180-Sekunden-Grenze für Aktionen wird hier nicht verändert.

## Umgesetzt und geprüft

- Die Hauptmeldung bleibt „Manuell gestoppt“, unabhängig vom reinen Alter des
  unveränderten STOP-Berichts. Sie behauptet keine sichere Stationsposition.
- Die überflüssige zweite Karte „Nächster Mähstart – Stopp am Mäher klären“
  entfällt. Nach einem neuen Gerätezustand erscheint die normale Planung wieder.
- Der Meldezeitpunkt bleibt in den vorhandenen Gerätedetails zugänglich.
- Keine Backend-, Konfigurations-, Berechtigungs- oder Geräteänderung.
- [205 Appack-Tests bestanden](tests.txt): neun und dreißig Minuten alter STOP,
  echte Ausfälle, Wasserpriorität, weiterhin verborgener Start sowie Aus- und
  Wiedereinblenden der Planungskarte. Quellen und CMS-Kopie synchron.
- [Gerenderte Offline-Vorschau bei 390 Pixeln](browser-check.json): korrekter
  Text, Zeitkarte und Startknopf verborgen, kein horizontaler Überlauf.
  Die im vorherigen Schritt nicht verfügbare Screenshot-Funktion wurde nicht
  erneut aufgerufen; keine native Geräteabnahme behauptet.
- CMS-Vorlage vor und nach dem Einfügen vollständig mit dem jeweiligen lokalen
  Quellstand abgeglichen. Speicherung am 12.09.2026 um 11:45 Uhr Berlin.
- [Öffentliche Auslieferung](publication.json) um 11:46:18 Uhr: alle vier
  geänderten Funktionen stimmen mit dem geprüften lokalen Stand überein.

Rückfall: CMS-Vorlage aus Vorgängercommit `219ccc3` wiederherstellen. Der lokale
Abzug heißt `appack-before.tpl`. Das verändert keine Geräteaktionen und hebt
keinen STOP auf. Der tatsächliche Wiederanlauf wurde nicht getestet.
