# SSV53 Ergebnisse – Integrationsstand

Arbeitsauftrag: Handball (nuLiga Brandenburg, Verein 33547) und Volleyball (KSV Oberhavel, 2. Kreisklasse Mixed) innerhalb der vorhandenen Appack-Oberfläche darstellen. Fußball bleibt ausschließlich ein Link auf FUSSBALL.DE.

Die Implementierung wird ausschließlich ergänzend in diesem Arbeitszweig integriert. Bestehende Mäher-, Bewässerungs- und Belegungslogik sowie deren Sicherheitsfreigaben bleiben unverändert.

## Ausgangspaket

`ssv53-ergebnisse-direkt-v1.zip`, erstellt am 23.09.2026. Enthält Appack-Template, `ssv_results/`, Azure-Blueprint und Offline-Tests. Das Paket ist kein vollständiges Azure-Deployment und darf die bestehende Function App nicht ersetzen.

## Freigabekriterien

- Original-Quellenabruf für beide Abteilungen erfolgreich; Testdaten sind keine Produktionsdaten.
- Zusätzliche Route und Timer registriert, ohne bestehende Funktionen oder Einstellungen zu ersetzen.
- Parser-, Integrations- und Browserprüfungen erfolgreich.
- Cache, Managed Identity und CORS im tatsächlichen Azure-Ziel geprüft.
- Appack-Veröffentlichung und Android-/iOS-WebView getrennt verifiziert.
- Regelmäßige Datenübernahme mit Quellenbedingungen/Zustimmung abgeglichen.

**Dieser Dokumentationscommit allein stellt nichts produktiv bereit.**
