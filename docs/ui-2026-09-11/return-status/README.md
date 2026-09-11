# Heimfahrt, Trockenmeldung und nächster Mähstart

Untersuchung vom 11.09.2026. Alle im Text genannten Uhrzeiten gelten für Berlin.
Stand dieses Dokuments: entwickelt und getestet; Veröffentlichungsnachweise
werden nach der tatsächlichen Bereitstellung ergänzt.

## Was die Daten belegen

Die [sechs Steuerungsbeobachtungen](controller-observations.json) stammen aus
Application Insights, 16:17–16:22 Uhr. Die zusätzlich geprüften 100 Zyklen ab
14:44 Uhr zeigen den Übergang von Mähen zur akkubegründeten Heimfahrt:

| Zeitpunkt | Gerätemeldung | Akku | Bewässerung | Belegungsplanung |
| --- | --- | --- | --- | --- |
| 16:17–16:18 | Mäht | 30 % | Aus, Daten frisch | Nächste Sperre 16:30–19:00 |
| 16:19 | Fährt zur Station | 30 % | Aus, Daten frisch | Kein sinnvoll nutzbares Fenster vor Training |
| 16:20–16:21 | In der Station | 27 % | Aus, Daten frisch | Parkvorlauf zur Trainingssperre |
| 16:22 | In der Station | 32 % | Aus, Daten frisch | Parkvorlauf zur Trainingssperre |

Die Bewässerung endete laut persistierter Bestätigung um 07:10 Uhr, die
150-minütige Trockenfrist um 09:40 Uhr. Es gibt in diesen Zyklen keinen Beleg
für eine neue Bewässerung. Die Quelle meldet `PARKED_IN_CS`, auch als der Akku
steigt; daraus wird kein erfundener bestätigter Zustand `CHARGING` erzeugt.

Training C läuft nominell 17:00–18:30 Uhr. Seine verbindliche Platzsperre
einschließlich beider Puffer ist 16:30–19:00 Uhr. Das nächste kanonische
Mähfenster beginnt um **19:00 Uhr**, Befehlsende 03:50 Uhr am Folgetag.

## Ursachen und Korrekturen

1. **Reproduzierbarer Zeitstempelkonflikt in der Nur-Lese-Abfrage:** Der
   Abfragebeginn wurde vor den externen Zugriffen festgehalten. Ein anschließend
   geladener neuerer Steuerungsstand konnte rückwärts mit diesem Zeitpunkt
   projiziert werden. Der negative Abstand wurde als mögliche Bewässerungslücke
   gewertet und erzeugte eine neue 150-Minuten-Frist nur in der Antwort.
   Jetzt wird ein neuerer Stand nicht rückwärts projiziert; seine physische
   Trockenfrist bleibt erhalten, die Freigabe bleibt bis zur nächsten konsistenten
   Abfrage geschlossen. Außerdem gehört der angezeigte Grund zum projizierten
   Stand. Echte lange Datenlücken erzeugen weiterhin die notwendige Sperre.
   Die historische einzelne HTTP-Antwort fehlt: Dieser Mechanismus ist
   reproduziert und zum Symptom passend, aber für genau diesen Screenshot
   nicht abschließend als Auslöser nachgewiesen.
2. **Falsche Reihenfolge in der Statusanzeige:** Eine Trockenmeldung konnte
   eine aktuelle Heimfahrt verdecken. Jetzt steht die bestätigte Heimfahrt
   vorn. Aktive Bewässerung, Störungen, unbestätigte Aktionen und manuelle
   Sperren behalten Vorrang. Bei einer schon aktiven Belegung bleibt zusätzlich
   die Warnung erhalten, solange der Mäher noch zurückfährt.
3. **Kurze irreführende Meldung „Automatik aktiv“:** Vor dem Parkvorlauf wurde
   die bereits bekannte kommende Belegung nicht erklärt. Die Anzeige erkennt
   jetzt bei eigener Automatik und frischen Daten eine Belegung innerhalb der
   nächsten 30 Minuten, wenn der kanonische Plan davor kein nutzbares Fenster
   enthält. Diese Anzeigeregel verändert keinen Plan oder Puffer. Außerhalb
   solcher Sperren zeigt ein geparkter Mäher mit niedrigem Akku seinen Zustand
   und Akkustand statt einer allgemeinen Automatikmeldung.
4. **Uhrzeit fehlte wegen Akkusperre:** Ein gesonderter Anzeigepfad darf bei
   bekannter Belegung das früheste spätere kanonische Fenster nennen, auch wenn
   Stationsankunft oder Ladeende noch fehlen. Beispiel: **„Frühester Mähstart
   19:00 Uhr – Nach der Platzsperre. Akku muss bereit sein.“** Das ist eine
   bedingte Zeit, keine Startfreigabe. Eine längere Trockenfrist, spätere
   Ladeprognose oder weitere Belegung verschiebt sie nach hinten. Bei fehlenden
   oder unbestätigten Daten, manueller Sperre und offenen Startanfragen bleibt
   die Zeit offen. Die strikte Startberechnung und Geräteberechtigungen werden
   dadurch nicht gelockert.

## Nachweise und Risiken

- [188 Appack-Tests](appack-tests.txt), davon 25 neue Fälle zu dieser Folge,
  fehlenden Daten, Startanfragen, manuellen Sperren und kanonischen Zeitfenstern.
- [1.453 Python-Tests und 482 Untertests](python-tests.txt) erfolgreich;
  Nur-Lese-Pipeline danach gesondert mit 8 Tests und 5 Untertests geprüft.
  Die Tests untersagen Speichern der Automatikdaten; Gerätezugriffe sind ersetzt.
- [Neun Browserfälle](browser-replay.json), jeweils 320/390/768 Pixel, ohne
  Überlauf oder JavaScriptfehler. [Heimfahrt](homeward-390.png),
  [Stationsankunft](docked-390.png), [Trainingspause](training-wait-390.png).
  Das sind rekonstruierte Ansichten mit Betriebswerten und synthetischen
  Berechtigungs-/Nebenfeldern, keine historischen App-Antworten oder Handybilder.
- Bestehende 24 Layoutfälle und 12 echte Browser-Verlaufsfälle erfolgreich,
  jeweils bei gesperrtem Netzwerk. Die bereits veröffentlichte Zurück-Navigation
  aus PR #77 bleibt enthalten.
- Begrenztes unabhängiges Review mit Luna durchgeführt; beide Hinweise zu
  offenen Startjournal-Einträgen und Heimfahrt bei Belegung wurden berücksichtigt.

| Risiko | Begrenzung / Abnahme |
| --- | --- |
| 19:00 wird als Zusage verstanden | „Frühester“ und sichtbarer Akkuvorbehalt; strikt getrennte Startprüfung |
| Unbekannte Bewässerung wird übersehen | Unbestätigte Wasserdaten sperren den neuen Forecast; lange Lücke bleibt gesperrt |
| Neuere Persistenz wird als ältere behandelt | Regression mit gleichzeitig neuerem Steuerungsstand; keine Schreibwirkung |
| Browserbild unterscheidet sich vom Handy | Drei Breiten geprüft; tatsächliche nächste Rückkehr am Handy noch zu beobachten |
| Neustart bei Azure-Installation | Vorab Stationszustand/Wasser aus prüfen; unveränderte Flags und installierte Dateien danach belegen |

## Reproduktion und Rückfall

```powershell
python scripts/sync_platzpflege_design.py --check
node --test tests/test_appack*.js
python -m pytest -q -p no:cacheprovider --basetemp=dist/pytest-return-status
python -m scripts.build_return_status_preview
node scripts/check_return_status.cjs
```

Der [vorherige Installationsstand](predeploy.json) ist laufend mit 16 Funktionen,
Manifest `0dd0c5a15b45e4b3eca7b3c334bcc4cc478edcaaf6ede7ca791eb30f8c85d291`.
Keine Änderung an Steuerungsmodus, Schaltern, Zeitpuffern, Bewässerung oder
Start-/Parkbefehlen ist Teil dieser Korrektur. Ein Rückfall der Oberfläche kann
mit der unmittelbar vor Veröffentlichung gesicherten Vorlage erfolgen. Ein
Backendrückfall nutzt das vorher nachgewiesene Paket
`dist/ladeprognose-fortschritt-release.zip`; keine Zustandsdaten zurückspielen,
keine offenen Geräteaktionen wiederholen. Vor und nach einem Host-Neustart
Gerätebeobachtungen und bestehende Sperren kontrollieren.

Offen: keine einzelne historische Status-HTTP-Antwort; direkter lesender Zugriff
auf die Zustandstabelle mit der vorhandenen Azure-Identität war mangels
Tabellenleserecht nicht möglich. Die Rollen wurden nicht verändert. Nachweise
stützen sich auf die realen Steuerungsprotokolle und isolierte Wiederholungen.
