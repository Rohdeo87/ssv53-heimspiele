# Prüfung und Freigabe der vereinfachten Anzeige

## Lokale Nachweise

| Prüfung | Ergebnis |
| --- | --- |
| Gesamte Python-Suite `python -m pytest -q tests` | 739 bestanden, 406 Untertests bestanden; 10,85 Sekunden |
| Alle Appack-Tests `node --test tests/test_appack*.js` | 88 bestanden, 0 fehlgeschlagen; [JUnit](appack-tests.xml) |
| Ladeende und betroffene Backendregressionen separat | 125 Tests und 15 Untertests bestanden; in Gesamtsuite enthalten |
| Template und TXT-Kopierfassung | Inhalt identisch; alle Skripte syntaktisch geprüft |
| Unabhängiges UI-Review | Fünf Zeit-/Prioritätsbefunde behoben und gezielt nachgeprüft |
| Unabhängiges Ladeprognose-Review | Mitternachtsfehler reproduziert und mit festem Anker abgesichert |
| Browser 390 × 844 | Zeitplan, Fehler, Datenverlust, Wiederherstellung und Plan-Dialog geprüft und aufgenommen |
| Browser 320 × 740 | Unbekannte Ladezeit; 305 px Inhalt bei 305 px nutzbarer Breite, kein horizontales Scrollen |
| Browser 1200 × 900 | 1185 px Inhalt bei 1185 px nutzbarer Breite, kein horizontales Scrollen |
| Native Appack-WebView / echtes Mobilgerät | Offen; Browseraufnahmen sind keine Geräteabnahme |

Neue Tests: `tests/test_charging_estimate.py` und
`tests/test_appack_simple_dashboard.js`. Die Sicherheitsprüfungen für Rollen,
Belegungsübersteuerung, Pause, ausgeblendete Aktionen und fehlende
Bedienfreigabe bleiben erhalten. Veraltete reine Textabgleiche wurden durch
Verhaltenstests ersetzt. UTC, Berlin und Los Angeles sind in den neuen
Zeitprüfungen enthalten; das unabhängige Review verwendete zusätzlich New York.
Mitternacht, beide Sommerzeitwechsel, ungültige Tage, fehlende sowie doppelte
Uhrzeiten und Pausen über Zeitwechsel sind geprüft.

## Historische Ladeabdeckung

Der bestehende private Export enthält 10.075 Zeilen und 10.071 eindeutige
Minutenbeobachtungen; 478 melden `CHARGING` in zwölf Abschnitten. Vier
abgeschlossene Abschnitte bestehen die strengen Qualitätsprüfungen mit
Startakku 73, 63, 9 und 64 %. Mangels Geräte-ID und Verbindungsfeldern erlaubt
dieser kleine Export nur die Analyse zeitlicher Abdeckung. Keine Rohdaten
oder Gerätekennungen werden mit diesen Dokumenten veröffentlicht.

Bei einem aktuellen Start mit 9 % reicht die Vergleichsmenge nicht. Ein späterer
Anstieg auf 64 % darf keinen neuen Prognoseanker eröffnen. Der Mitternachts-Repro
zeigte zunächst 00:00 Uhr und im fehlerhaften Zwischenstand nach Wegfall eines
alten Vergleichs plötzlich 00:10 Uhr. Der Regressionstest rekonstruiert das
komplette Modell nach dem tatsächlichen Wechsel des Statistikfensters,
einschließlich JSON-Rundlauf; korrigiert bleibt die Prognose unbekannt.

Historische Evidenz wird höchstens fünf Minuten gecacht, Live-Akku und
Gerätezustand je Antwort geprüft. Fehlende Werte werden nicht als gesunde
Nullwerte interpretiert. Datenlücke, Stagnation, abgebrochener Ladeabschnitt,
Fehler, Offlinezustand, fehlende Vergleichstage und abgelaufene Prognose sind
als Fehlerinjektionen abgesichert.

## Risiken

| Risiko | Wahrscheinlichkeit / Auswirkung | Erkennung und Prävention | Wiederherstellung / Restrisiko |
| --- | --- | --- | --- |
| Schätzung als feste Zusage verstanden | Möglich / falsche Erwartung | „Voraussichtlich“, konservative Voraussetzungen, keine Steuerungswirkung | Bei Abweichung „Noch offen“; Prognosefehler im Parallelbetrieb messen |
| Zu wenige Ladevorgänge | Im Export häufig / Ladezeit bleibt offen | Mindestmenge am festen Startanker | Weitere störungsfreie Verläufe sammeln; keine erfundene Laderate |
| Historie wird nachträglich ergänzt | Möglich / Zeitpunkt verändert sich | Gleiche Geräte-ID und Qualitätsregeln | Historie prüfen; keine dauerhafte Prognosepersistenz in diesem Umfang |
| Neues Template auf altem Backend | Möglich / fehlende Angaben | Versionspaar und Statusvertrag vor Veröffentlichung prüfen | Vorheriges Template wiederherstellen; Backend-Rückfall gemäß Auditplan |
| Falsche Gerätezeitzone | Real reproduziert / falsche Aktionszeit | Durchgängige Berlin-Konvertierung, DST-Mehrdeutigkeit abweisen | Eindeutige Uhrzeit wählen; native Abnahme noch offen |
| Alte Daten wirken aktuell | Bei Netzausfall möglich / Fehlbedienung | Startzeiten entfernen, Karten markieren, Bedienung sperren | Aktualisieren und nötigenfalls Anlage prüfen |

## Gestufte Einführung

1. Vorschau mit Normalbetrieb, offenem Ladeende, Pause, Fehler und
   Verbindungsabbruch abnehmen. Keine Geräteaktion nötig.
2. PR-Checks und Paket-Dateihashes am dokumentierten Quellstand kontrollieren.
   CI-Quellpaket und FULL_FAILSAFE-Paket bleiben verschiedene Artefakte;
   ein Build bedeutet keine Installation.
3. Auf freigegebener Testumgebung Backendfeldvertrag, Rollen und mobile WebView
   als Versionspaar prüfen. Mindestens drei weitere vollständige Ladevorgänge
   an zwei Tagen ausschließlich beobachten; Abweichungen und Verfügbarkeit
   der Prognose erfassen. Noch kein Live-Nachweis vorhanden.
4. Erst nach gezielter Freigabe Backend bereitstellen und Appack übernehmen.
   Abbruch bei falschem Datum, zu früher Startprognose, verschleierter Sperre,
   fehlender Fehlermeldung oder unklarem Versionspaar.
5. UI-Rückfall durch vorherige CMS-Version. Beim Backend-Rückfall über den
   gesamten Audit-PR zusätzlich Gerätewarteschlangen, native Pläne,
   Pending-Zustände und Datenmigration nach Auditplan berücksichtigen.

Keine produktiven Gerätebefehle, CMS-Veröffentlichung, Infrastrukturänderung,
Beobachtung im Parallelbetrieb oder Livepilot erfolgten in diesem UI-Schritt.
Eine zusätzliche Mähzeit- oder Wasserersparnis wird dafür nicht beansprucht.

Die Vorschau entsteht mit `python scripts/build_simple_dashboard_preview.py`
aus dem tatsächlichen Template. Der Quellenhash steht in
`appack-preview-provenance.json`. Netzwerkzugriff ist per CSP gesperrt;
Vorschau-Zugangsdaten sind synthetisch und alle POST-Anfragen werden abgewiesen.
