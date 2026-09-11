# Zurück innerhalb der Platzpflege

Am 11.09.2026 um 15:16 Uhr (Berlin) in Appack veröffentlicht. Ausschließlich
Oberflächennavigation; keine Azure-Installation und keine Gerätebefehle.

## Ursache und Änderung

`pfGo()` blendete bisher Bereiche ein und führte nur eine eigene Liste im
Arbeitsspeicher. Der Browser erhielt keine Einträge für Unterseiten. Deshalb
konnte die Handy-Zurück-Taste diese Schritte nicht berücksichtigen.

Die Unterseiten und die Schritte „Bewässerung pausieren“ / „Nächsten Lauf
anpassen“ verwenden jetzt die History API. Bildschirm-Zurück und Browser-Zurück
nutzen denselben Verlauf. Auch Wechsel über die untere Navigation zählen als
Seitenwechsel. Wiederholtes Öffnen derselben Seite und Aktualisieren erzeugen
keine weiteren Schritte. Neuladen stellt die zuletzt besuchte Unterseite wieder her.

Offene Bestätigungen werden beim Zurückgehen geschlossen und ihre noch nicht
abgeschickten Angaben verworfen. Der Wechsel vom Beenden-Auswahldialog zur
Bestätigung belegt nur einen Schritt. Vorwärtsnavigation oder Neuladen dürfen
keine frühere Bestätigung erneut öffnen oder einen Befehl auslösen.
Bereits abgeschickte Aktionen werden durch Navigation nicht zurückgenommen.

Die anfängliche Übersicht erhält keinen künstlichen zusätzlichen Eintrag:
Von dort kann der normale Zurückweg aus der Platzpflege weiterhin funktionieren.
Appack-URLs einschließlich vorhandener Parameter und Fragmente bleiben erhalten.
Im Verlauf werden nur Seitenkennung, Unteransicht und Scrollposition gespeichert;
keine PIN, Geräteaktion, Bestätigung oder Berechtigung.

Grundlage: [MDN History API](https://developer.mozilla.org/en-US/docs/Web/API/History_API/Working_with_the_History_API).
Die [Android-WebView-Dokumentation](https://developer.android.com/develop/ui/views/layout/webapps/webview)
beschreibt die notwendige Weiterleitung der Zurück-Taste an den WebView-Verlauf.
Eine Appack-spezifische native Zurück-Schnittstelle war in den lokalen Quellen
nicht vorhanden. Der native Appack-Quellcode und das Handy waren nicht zugänglich.
**Ob die installierte Appack-Version die Taste korrekt weitergibt, muss deshalb
noch auf dem Handy bestätigt werden.** Falls Appack den WebView immer direkt
schließt, ist zusätzlich eine Anpassung durch den Appack-Anbieter erforderlich.

## Nachweise

- [163 bestehende Appack-Tests bestanden](appack-tests.txt).
- [12 Browserfälle](back-browser-check.json): jeweils 320, 390 und 768 Pixel,
  echter Browser-Verlauf, gemischte Bildschirm-/Browser-Zurückbedienung,
  Vorwärtsnavigation, Neuladen, Aktualisieren, untere Navigation, beide
  Bewässerungs-Unteransichten, Statistiken, Escape und Dialogwechsel.
  Keine Geräteanfragen bei diesen Navigationstests; Netzwerk blockiert.
- [24 Ansichts-/Bedienkombinationen](browser-check.json) ohne Fehler.
  Der Vorschau-Aufbau setzt jeden unabhängigen Bildfall ausdrücklich auf die
  Übersicht zurück, da ein gewöhnliches Neuladen jetzt die Unterseite erhält.
- Begrenztes unabhängiges Review mit Luna: Unteransichten, Dialogwechsel und
  Abbruchverhalten geprüft. Der initiale Ausstieg bleibt absichtlich frei.
- [Veröffentlichung](publication.json): vollständiger Editorvergleich nach
  CMS-Neuladen erfolgreich; ausgelieferte CSS- und JS-Blöcke gleich zur Vorlage.
  Vorherige Vorlage: [appack-before.tpl](appack-before.tpl).
- [Browserbild nach Zurück aus den Statistiken](back-water-390.png), synthetische
  Werte, keine Geräteaufnahme. Sonstige Bilder sind lokal reproduzierbar.

Reproduktion (Playwright in der Umgebung erforderlich):

```powershell
$env:SSV53_UI_OUTPUT='docs/ui-2026-09-11/phone-back'
python scripts/sync_platzpflege_design.py --check
python -m scripts.build_wunschdesign_preview
node scripts/check_platzpflege_back.cjs
node scripts/capture_wunschdesign.cjs
node --test tests/test_appack*.js
```

Handy-Abnahme: Platzpflege schließen und neu öffnen; Sonstiges → Mähroboter →
Schnitthöhe öffnen. Dreimal Zurück sollte Mähroboter, Sonstiges und Übersicht
zeigen. Zusätzlich Bewässerung → Zeitplan → Nächsten Lauf anpassen und eine
unbestätigte Aktion zurücknehmen. Navigation darf keine Aktion abschicken.

Rückfall: gesicherte Vorlage in derselben Appack-Seite wiederherstellen und
deren Auslieferung vergleichen. Keine Änderungen an Gerätesperren oder
Steuerungseinstellungen erforderlich.
