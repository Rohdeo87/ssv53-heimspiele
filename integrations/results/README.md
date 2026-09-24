# SSV53 Ergebnisse: Handball + Volleyball

**Implementiert im Arbeitszweig, nicht produktiv veröffentlicht.** Fußball bleibt ein normaler Link auf FUSSBALL.DE. Keine Funktionen für Mäher, Beregnung oder Platzbelegung werden verändert.

## Enthalten

- `ssv_results`: begrenzte, robots-konforme Quellenabrufe, dynamische Mannschafts-/Staffelzuordnung, Saisonprüfung und Erhalt letzter erfolgreicher Daten.
- `results_blueprint.py`: öffentlicher GET-Endpunkt `/api/ssv-results?sport=handball|volleyball` aus einem privaten Blob-Cache; separater stündlicher Timer (Minute 17), standardmäßig deaktiviert.
- `appack/Ergebnisse_Tabellen.tpl`: selbstständiges Appack-HTML im SSV-Design, Mannschaftsauswahl, Spielkarten, Handballtabellen, Original-Volleyballgrafik, mobile Bedienung und lokale Rückfallkopie. Keine Demoergebnisse in der Produktivvorlage.
- `prepare_integration.py`: erstellt einen prüfbaren Quellcode-Overlay und einen minimalen Patch für den **existierenden** FunctionApp-Einstieg. Verändert die Originaldateien nicht; prüft per AST, dass die vorhandene Programmlogik unverändert bleibt.
- Tests mit synthetischen HTML-Quellen; ein echter Quellencheck ist getrennt von Offline-Tests.

## Prüfen

```sh
python -m pip install -r integrations/results/requirements.txt pytest playwright
cd integrations/results
python -m pytest -q tests
python -m playwright install chromium
python tests/browser_check.py
python -m ssv_results --output .test-output/live
```

Der letzte Befehl kontaktiert nur die beiden Ergebnisquellen und führt kein Deployment aus. Bei Sperren/robots-Verboten wird nicht umgangen, bei Fehlern der vorhandene Stand erhalten. Ein Live-Check ist kein Nachweis einer Anbieterfreigabe zur dauerhaften Weiterveröffentlichung.

## Einbau vorbereiten (aus dem Repository-Stamm)

```sh
python integrations/results/prepare_integration.py --repo . --output dist/results-review
```

Mit nachgewiesenem Endpunkt zusätzlich `--api-url https://BESTAETIGTER-HOST.azurewebsites.net/api/ssv-results` verwenden. Das ist ein Platzhalter, kein bereits vorhandener Endpunkt.

Das Ergebnis enthält den vollständigen **geänderten** Einstiegspunkt, den minimalen Patch, die zusätzlichen Module, die Appack-Vorlage und SHA-256-Nachweise. `source-overlay` ist **kein vollständiges Function-App-Paket**. Vor dem Deployment müssen die Zusatzdateien auch in die bestehenden Paketierungs-/Provenance-Whitelists aufgenommen und sämtliche bestehenden Regressionstests ausgeführt werden. Keine vorhandenen Hardware-Freigaben oder App-Einstellungen überschreiben.

## Benötigte Azure-Konfiguration

Privaten Blob-Container `ssv53-results` anlegen; der Function-Managed-Identity auf diesen Container `Storage Blob Data Contributor` gewähren. Keine Storage-Schlüssel ins HTML. Nur neue Einstellungen ergänzen:

- `SSV_RESULTS_STORAGE_ACCOUNT_URL=https://BESTEHENDER-ACCOUNT.blob.core.windows.net`
- `SSV_RESULTS_CONTAINER=ssv53-results`
- `SSV_RESULTS_ENABLED=false` bis Quellenabruf, Berechtigung und Cache nachweislich funktionieren.
- optional `SSV_RESULTS_MANAGED_IDENTITY_CLIENT_ID` für eine user-assigned Identity; die vorhandene Identity-Architektur beachten.
- optional `SSV_RESULTS_USER_AGENT` mit einem vom Verein freigegebenen technischen Kontakt.
- optional `SSV_RESULTS_SEASON_START=2026`; ohne Vorgabe gilt Saisonwechsel im Juli (Europe/Berlin).

Erst nach geprüftem Gesamtpaket, Quellenbedingungen/Anbieterfreigabe und Azure-Test aktivieren. Die zusätzliche Route erlaubt CORS ausschließlich für öffentliche Ergebnisdaten. **Keine globale CORS-Freigabe für die gesamte bestehende App setzen.** Das Zusammenspiel mit vorhandenem Plattform-CORS muss im Ziel geprüft werden.

## Appack-Veröffentlichung

Die existierende Ergebnisvorlage im CMS zuerst sichern. Die generierte `.tpl` erst veröffentlichen, wenn beide Endpoint-Aufrufe echte Daten liefern; auf Android und iOS prüfen. Die Vorlage wird nicht durch einen GitHub-Merge automatisch im Appack-CMS veröffentlicht. Es liegt hier kein angemeldeter Appack-Schreibzugang vor.

## Datenqualität / Grenzen

Volleyballtabellen bleiben unveränderte offizielle Grafiken. Keine OCR, keine erfundene Neuberechnung, keine falschen 0:0. Offene und abgesagte Spiele, zurückgezogene Teams und Mini-Staffeltermine werden gekennzeichnet. Mini-Staffeltermine sind kein automatischer Teilnahmenachweis für den SSV. Es werden keine Spielerlisten, privaten Kontaktdaten oder Schiedsrichterberichte übernommen.

Die GitHub-Prüfung verwendet keine Azure-Zugangsdaten, keine Hardware-APIs und keinen Deploy-Befehl. Offline-Tests ersetzen keine Prüfung mit echten Websites, Managed Identity, Appack-WebView und produktiven CSS-Dateien.
