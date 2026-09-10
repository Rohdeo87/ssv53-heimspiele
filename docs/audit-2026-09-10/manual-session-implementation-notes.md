# Technische Vorbereitung der manuellen Vorrangregel

Stand 10.09.2026. Basis `4c2c022c761aaad0d5ff65aed78312e427bc3952`.
[PR 58](https://github.com/Rohdeo87/ssv53-heimspiele/pull/58) ist nach erfolgreicher
Quellcode- und Paketprüfung des exakten Heads
`6c27494d793606e14e095a9b51ebded4a7de7e05` übernommen. Das ist ein Repository-
Nachweis, kein Produktionsdeployment. Entwicklung liegt nun auf
`feature/manual-session-control-20260910`.

Die [Betreiberentscheidungen](manual-override-policy.md) sind geklärt:
manueller Start bis zur nächsten Ladefahrt nur mit Platzfrei-Bestätigung für
Training/Spiele, Trocknung nur mit Vor-Ort-Bestätigung, Wasserentscheidung je
Konflikt, Parken bis ausdrücklicher Freigabe und Husqvarna nur nach
App-Vorbereitung/-Bestätigung. **Die technische Implementierung ist noch in
Arbeit; daraus folgt keine Produktionsfreigabe.**

## Bestehende Wege weiterverwenden

`platzwart_console.request_action()` schreibt bisher einen einzelnen
kurzlebigen `operator_request_*`. Der vollständige Steuerungspfad bearbeitet
darüber auch `START_MOWING`. Das neuere `operator_commands_json` ist absichtlich
auf Parken und Schnitthöhe im Bedienmodus begrenzt. Es soll keine zweite
konkurrierende Startwarteschlange entstehen.

Der neue begrenzte Datensatz `AutomationState.manual_session_json` trägt
die dauerhafte Autorisierung und ihre Gültigkeit tragen. Vorgesehene Daten:
Version, Sitzungskennung, Generation, exaktes Gerät, Erstellzeit, Zustand,
bestätigte Belegungskennungen, konkrete Wasser-Konfliktkennung und Auswahl,
sowie beobachtete Abfahrt und Beendigungsbedingung. Der bestehende einmalige
Startauftrag und seine persistente Startreservierung werden an dieselbe
Sitzung und Generation gebunden. Fehlende oder fehlerhafte Pflichtdaten dürfen
keine Startfreigabe erzeugen.

Die Session darf nicht bereits durch den Stationsstatus beim Anlegen enden.
Nach beobachteter Ausfahrt beendet die nächste beobachtete Ladefahrt die
manuelle Mähfreigabe. Datenlücken und Neustart brauchen ausdrücklich getestete
Wiederherstellung; eine verlorene Befehlsantwort führt nicht zur Wiederholung.
Bestätigungen gelten nur für die tatsächlich angezeigten Belegungen und den
konkreten Wasser-Konflikt, nicht pauschal für spätere Änderungen.

## Vor der Ausführung zu schließende Lücken

Das unabhängige Review fand in `mower/full_failsafe.py` folgende Unterschiede:

| Weg | Bestehende Absicherung | Noch erforderliche Arbeit |
| --- | --- | --- |
| Mäherstart am Ende des Zyklus | Persistente Reservierung; erneute Zustands- und Zeitprüfung durch `prepare_start_dispatch()` auch nach der Anmeldung vor dem Senden. | Session und Generation in die vorhandene Prüfung aufnehmen. |
| Haupt-Parkpfad, Hydrawise-Halt und Continuous-Bootstrap | Geräteaufruf erfolgt vor dem abschließenden persistierten Befehlsdatensatz. | Vorherige Reservierung und gemeinsame Prüfung unmittelbar vor dem Senden. |
| Parken bei unbekanntem vorherigen Startausgang | Reservierung vorhanden, aber keine erneute Prüfung unmittelbar vor dem Senden. | Auch diesen Pfad an die gemeinsame Prüfung binden. |
| Wasserstart | `START_RESERVED` und erneute Zustandsprüfungen vorhanden. | Gemeinsame Prüfung direkt vor dem HTTP-Aufruf, auch bei Verzögerung nach der bisherigen Prüfung. |
| Wasserstopp | Zentraler Pfad reserviert `STOPPING`; keine gemeinsame Prüfung direkt vor dem Senden. | Exakte Zone und Reservierung erneut prüfen; Ende weiter beobachten. |
| Native Zeitpläne aussetzen oder wieder zulassen | Nur der Koordinationspfad reserviert einzelne Zonen vollständig; andere Pfade wiederholen beziehungsweise speichern erst nach dem Aufruf. | Jede Zonenänderung vorab reservieren; unbekannte Ergebnisse anhand neuer Beobachtungen klären. |
| Nicht zentral gestartete Bewässerung stoppen | Der zentrale Stopp setzt derzeit eine eigene Ablaufphase voraus. | Eigene persistente Konflikttransaktion mit vollständigem Zonen- und Plannachweis. |

Ein früher Session-Check am Anfang des Zyklus genügt deshalb nicht. Alle
ausführenden Schnittstellen brauchen dieselbe unmittelbar vor dem Senden
ausgeführte Prüfung von Reservierung, Generation, Ziel, frischen Daten und
gültiger Konfliktentscheidung. Ein Lockablauf allein beendet keinen bereits
übermittelten Geräteauftrag.

## Parknachweis und laufende Bewässerung

`_park_confirmation_ready()` muss vor einem neuen Wasserstart und vor der
Wiederzulassung des geräteeigenen Wasserplans auch den dauerhaften Parkmodus
prüfen. Der bestehende allgemeine Aufruf liegt jedoch vor den Phasen
`START_RESERVED`, `RUNNING` und `STOPPING`. Eine unbedachte Verschärfung genau
dort würde bei einem Mäher-Moduswechsel den Wasserstopp und die Erkennung des
tatsächlichen Wasserendes verhindern. Diese Verarbeitung muss unabhängig vom
Mähermodus erreichbar bleiben; nur neue Starts benötigen die Startfreigabe.

Bei der Wahl zugunsten des Mähers muss der konkrete ursprüngliche Wasserplan
gespeichert und gegen zusätzliche Ausführung gesichert sein. Für einen
unbekannten oder nicht zentral gestarteten Lauf sind vollständige frische
Allowlist-Daten, eindeutige aktive Zone und stabile Planidentität erforderlich.
Aussetzen, Stopp, bestätigtes Ende und der spätere Mäherstart sind getrennte
persistent beobachtete Schritte. Mehrere oder unbekannte aktive Zonen dürfen
nicht durch eine erfundene eindeutige Zuordnung ersetzt werden.

Eine neue Bewässerungsplanung oder neue Konflikt-Occurrence benötigt eine neue
Auswahl. Die nachgeholte Wasserversorgung und die Betriebsgrenze 03:30–08:00 Uhr
müssen berücksichtigt werden; „übersprungen“ bedeutet nicht „Bedarf erfüllt“.

## Grenze der Husqvarna-Erkennung

Eine Antwort auf unseren eigenen REST-Befehl kann eine Befehlskennung liefern.
Die untersuchten Status- und Ereignisschnittstellen liefern aber keinen
verlässlichen Herkunftsnachweis für jede fremde Bedienaktion. Ein erneuter
Parkbefehl während eines bereits bestehenden gleichen Parkzustands lässt sich
aus dem Status nicht sicher erkennen. Deshalb verlangt die beschlossene Regel
für Husqvarna-Eingriffe eine Vorbereitung und Bestätigung über die App; ein
Status allein ordnet keine Herkunft oder Bedienperson zu.

## Erforderliche gezielte Prüfungen

Neben den vorhandenen Regressionstests sind zwei konkurrierende Sessions,
Neustart und verlorene Antworten, Generationstausch während der Anmeldung,
manuelles Parken nach einer Startreservierung, neue Belegung nach Bestätigung,
Ausfahrt und anschließende Ladefahrt sowie jeder Schritt einer unterbrochenen
Bewässerungstransaktion zu prüfen. Neue und native Bewässerungstermine dürfen
nicht doppelt ausgeführt werden. Diese Tests bleiben ohne Gerätezugriff.

## Entwicklungsstand nach Abschlussprüfung

Die Entwicklung auf `feature/manual-session-control-20260910` enthält die
bestätigten Regeln und wurde lokal mit 1.337 Python-Tests plus 449 Untertests,
135 Appack-Tests und einer Browserprüfung auf drei Bildschirmbreiten geprüft.
Die neuen Tests decken Park/Freigabe, Neustart, neue Konflikte, unterbrochene
Geräteanfragen und die Reihenfolge Schutzparken → bestätigter Wasserstopp →
bestätigtes Aussetzen des ursprünglichen Wasserplans ab.

Die vollständigen Belege, die unabhängige Nachprüfung und die verbleibende
betriebliche Prüflücke zur Aktualität ruhender Gerätemeldungen stehen im
[Lieferbericht](manual-control-delivery.md). Diese Nachweise sind Tests und
Simulationen, keine Live-Abnahme. Eine Aktivierung ist hier nicht behauptet.
