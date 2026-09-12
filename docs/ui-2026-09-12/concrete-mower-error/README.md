# Konkrete Mäherstörung statt Sammelmeldung

12.09.2026: Der Nutzer zeigte die Sammelmeldung „Mäher braucht Hilfe“. Die aktuellen, nur gelesenen Azure-Zyklen melden `ERROR`, `NOT_APPLICABLE`, `MAIN_AREA`, verbunden, Fehlercode **9**. Alle sieben Bewässerungszonen waren aus. Beleg: [recent-cycles.json](recent-cycles.json), neuester Zyklus 14:13 Uhr Berlin.

Code 9 bedeutet **Trapped**, nicht Upside down (Code 10). Die Zuordnung wurde direkt in der offiziellen [Husqvarna-API-Dokumentation](https://developer.husqvarnagroup.cloud/apis/Automower+Connect+API?tab=status%20description%20and%20error%20codes) gelesen und in [manufacturer-extract.json](manufacturer-extract.json) festgehalten. Die [Anleitung des 580 EPOS](https://www.husqvarna.com/hbd/tdrdownload/v2/pub000094011/doc000256022/OM/cvhf_pfVzveeiQWyoW3cKRBKUno?httproute=True) nennt für Trapped Hindernisse, die das Wegfahren verhindern. Die Meldung beweist nicht, welches konkrete Hindernis vor Ort vorliegt.

Die Oberfläche ersetzt bislang fast alle Fehler durch eine generische Meldung, obwohl `errorCode` und teilweise `errorMessage` vorhanden sind. Jetzt werden belegte häufige Codes direkt und in einfachem Deutsch angezeigt. Hier:

> **Mäher steckt fest**
>
> Bitte vor Ort prüfen und Hindernisse entfernen.

Bei einem stehenden Mäher mit aktiver Störung verschwinden wirkungslose Park-/Startbuttons und die unklare nächste Startzeit. Bei gemeldeter Bewegung bleibt die schützende Parkaktion erreichbar; bei laufender Bewässerung bleibt Beenden erreichbar. Historische Fehlercodes werden nach einem fehlerfreien Folgezustand nicht als aktive Störung ausgegeben. Unbekannte Codes bleiben erkennbar, ohne eine Ursache zu erfinden.

Validierung: **223 Appack-Tests bestanden**, einschließlich Code 9/10, unbekannter Fehler, Fehlerbehebung, veralteter Meldung und Schutzaktionen. Die 390-px-Browservorschau zeigte Titel und Handlung, ohne Parkknopf oder unbekannte Startzeit. Fixture: [fixture.json](fixture.json). Screenshot-Aufnahme war technisch nicht verfügbar; es wird keine native Geräteabnahme behauptet.

Die aktive CMS-Vorlage wurde vor dem Schreiben exakt gegen den vorherigen veröffentlichten Stand abgeglichen und gesichert. Veröffentlichung um **14:26 Uhr Berlin** mit anschließendem Vergleich der betroffenen ausgelieferten Funktionen: [publication.json](publication.json). Ausgelieferter LF-Template-Hash: `c4d50cda346fbfb10aa08543de57dbba066420cc3ebeb2b63b6d1c72bac7d35a`.

Dies ist eine Anzeigekorrektur. Kein Mäherstart, Park- oder Wasserbefehl wurde gesendet; keine Backendsoftware oder Schutzkonfiguration wurde produktiv verändert. Die Vor-Ort-Stationsbestätigung ist separat in Entwicklung und darf diesen aktiven Fehler nicht übergehen.

Abschlussabfrage: [final-observations.json](final-observations.json) zeigt um **15:03 Uhr Berlin** bereits `MOWING`, `IN_OPERATION`, Fehlercode 0 und alle sieben Bewässerungszonen aus. Die Gerätefehleranzeige soll dann verschwinden. Das ist eine Beobachtung der bisherigen produktiven Software mit unverändertem Manifest, kein Live-Nachweis der zusätzlich entwickelten Stationsbestätigung.
