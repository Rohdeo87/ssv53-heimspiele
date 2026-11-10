# Mäher: Wartung / Winterpause

Stand: 10.11.2026. Implementierter, geprüfter Entwurf. Weder nach Azure noch
in das Appack-CMS veröffentlicht; am echten Mäher wurde nichts geändert.

## Bedienung nach Veröffentlichung

Unter **Platzpflege → Sonstiges → Mähroboter → Wartung → Wartung / Winterpause**:

Die Mähroboter-Seite zeigt den Menüpunkt **Wartung**. Der Schalter ist erst
auf dieser Unterseite sichtbar.

1. Den Mäher sicher parken und die Bestätigung abwarten. Vor der Einlagerung
   den Mäher vor Ort ausschalten. Der Wartungsschalter sendet keinen Stoppbefehl.
2. Als Grund **Winterpause**, **Wartung** oder **Defekt** wählen, den Schalter
   einschalten und die Nachfrage bestätigen.
3. Der Mäher bleibt dauerhaft außer Betrieb, auch nach App-Schließen oder einem
   Serverneustart. Die Übersicht zeigt den gewählten Grund anstelle einer
   Startprognose. Erwartete Offline-Meldungen erzeugen keine Mäherwarnung.
4. Zur Rückkehr den Mäher wieder in die Station bringen und eine frische,
   verbundene, fehlerfreie Meldung im Modus HOME abwarten. Dann den Schalter
   ausschalten und **Mäher wieder in Betrieb nehmen** bestätigen.

Die Wiederinbetriebnahme sendet selbst keinen Startbefehl. Danach gelten die
bisherigen Freigaben und Sicherheitsprüfungen; eine zuvor vorhandene manuelle
Parksperre bleibt bestehen. Es gibt kein automatisches Ende der Winterpause.

## Sicherheitsgrenzen

- Der bestehende allgemeine `maintenance_mode` bleibt unverändert. Der neue
  Mäherschalter hat eigene, persistente Felder in `MowerAutomationState`.
- Start, Parken, Schnitthöhe, Klingenzähler und manuelle Startvorbereitungen
  sind während der Pause gesperrt, auch bei alten App-Seiten. Die vorhandene
  Platzwart-Anmeldung schützt die neue Aktion; jede Änderung wird protokolliert.
- Ungesendete Mäheraufträge werden verworfen, START-Sitzungen beendet und
  ungenutzte Koordinierungsentwürfe aufgehoben. Versandbelege und unklare
  Transportergebnisse werden erhalten. Ein unbestätigter Start oder ein
  ungeklärter Gerätebefehl muss vor dem Umschalten geklärt werden.
- Die Umschaltung nutzt den bestehenden Compare-and-Swap-Speicher und einen
  eigenen Schalterstand. Doppelte Requests sind idempotent; veraltete Requests
  und späte Befehle nach einem zwischenzeitlichen Ein-/Ausschalten werden abgewiesen.
- Laufende Bewässerungsabläufe bzw. Wasserkonflikt-Transaktionen müssen vor
  dem Umschalten beendet sein. Der Schalter ändert keine Bewässerungspläne,
  Ventile, Trainingsschalter oder E-Mails. Bewässerungsstopps und deren
  Bestätigung funktionieren auch bei offline eingelagertem Mäher.
- Ein eingelagerter Mäher zählt **nicht** automatisch als sicher geparkt.
  Bewässerungsstarts benötigen weiterhin sämtliche bisherigen Nachweise.
  Fehlende Bewässerungsdaten und echte Störungen bleiben sichtbar. Ein frisch
  als fahrend gemeldeter Mäher erzeugt trotz Winterpause eine Warnung.

## Veröffentlichung vorbereiten

Keine neue Azure-Funktion, App-Einstellung oder Datenmigration erforderlich.
Neue Zustandsfelder fehlen im bisherigen Zustand und sind standardmäßig aus.
Alle vier Paketbuilder enthalten das neue Modul `mower/service_mode.py`.
Backend und Appack-Vorlage müssen gemeinsam ausgeliefert werden. Eine alte
Backend-Version darf eine aktive Pause nicht verwalten: alte Zustandsmodelle
könnten die neuen Felder beim Schreiben verwerfen. Bestehende Live-Pakete und
CMS-Anpassungen sind bei einer späteren Veröffentlichung gezielt zu erhalten.

## Prüfung und Vorschau

Die Python-Tests verbieten Netzwerkzugriffe. Die zusätzlichen Sicherheitsfälle
prüfen unter anderem parallele Updates, abgelaufene Bestätigungen, langsame
Authentifizierung, unklare Starts, verworfene Altaufträge, Offline-Winterpause
und Bewässerungsstopps samt physischer Abschlussbestätigung mit simulierten
Geräteantworten.

```sh
.venv/bin/python -m pytest -q
node --test tests/test_appack*.js
.venv/bin/python scripts/sync_platzpflege_design.py --check
.venv/bin/python -m scripts.build_mower_service_preview
python3 -m http.server 8765 --bind 127.0.0.1 --directory /tmp/ssv53-service-preview
```

Die Vorschau unter `http://127.0.0.1:8765/appack-preview.html` enthält ausschließlich
Beispieldaten und blockiert externe Netzwerkzugriffe per CSP. Mobile Prüfung
bei 320 und 390 Pixeln: kein horizontaler Überlauf, keine JavaScript-Fehler;
Abbrechen sowie Ein-/Ausschalten mit simulierten API-Antworten geprüft.

![Winterpause in der Übersicht](winterpause-390.png)
![Eigener Menüpunkt Wartung bei Mähroboter](menue-maehroboter-390.png)
![Wartungsschalter](schalter-390.png)
![Bestätigung der Wiederinbetriebnahme](bestaetigung-390.png)
