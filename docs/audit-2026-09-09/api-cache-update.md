# A01 – Gemeinsame Hydrawise-Lesekoordination

Stand: Entwicklungsprüfung vom 09.09.2026. Implementiert und mit vollständig ersetzten HTTP-/Speicherzugriffen getestet. Nicht installiert, nicht aktiviert, nicht im Parallelbetrieb beobachtet und nicht mit einem realen Hydrawise-Abruf geprüft. Standard bleibt `HYDRAWISE_STATUS_CACHE_MODE=OFF`.

## Befund und abgegrenzte Lösung

Die bisherigen Aufrufstellen lesen `statusschedule.php` unabhängig: der gemeinsame Statuspfad in [dry_run.py](../../mower/dry_run.py), der eigenständige Plangenerator in [generate_schedule.py](../../mower/generate_schedule.py) und die manuelle Fehlerbereinigung in [irrigation_recovery.py](../../mower/irrigation_recovery.py). Sie teilen im bisherigen Transport weder ein Abrufbudget noch eine laufende Abfrage. Die Codeanalyse belegt diese konkurrierenden Zugriffsmöglichkeiten; sie beweist nicht, dass ein konkreter früherer Ausfall durch HTTP 429 verursacht wurde.

Ein Prozesscache allein löst das Problem bei mehreren Hosts nicht. Der neue kleinste gemeinsame Integrationspunkt liegt deshalb unmittelbar über `fetch_status`, mit einer getrennten, bedingt aktualisierten Cachezeile je ausdrücklich identifiziertem Controller. Die rohe Transportfunktion bleibt für den ausgeschalteten Bestandspfad erhalten.

Hunter dokumentiert `nextpoll` als Sekunden bis zur nächsten Anfrage an den Statusendpunkt; zu frühe Anfragen können HTTP 429 auslösen. Der Status enthält einen Unix-Zeitpunkt und relative Zonenzeiten. Diese Angaben müssen zusammen erhalten bleiben. Der Controller besitzt eine eindeutige Kennung, die bei Konten mit mehreren Controllern erforderlich ist. Quelle: [Hydrawise REST API 1.6, Seiten 1–3](https://www.hunterindustries.com/sites/default/files/2024-10/Hydrawise%20REST%20API%20Ver%201.6_0.pdf), am 09.09.2026 über die [Herstellerseite](https://www.hunterirrigation.com/support/hydrawise-api-information) geprüft. Die Implementierung berücksichtigt außerdem `Retry-After` als Sekundenwert oder HTTP-Datum nach [RFC 9110, Abschnitt 10.2.3](https://www.rfc-editor.org/rfc/rfc9110.html#name-retry-after).

## Wichtige Freigabegrenze

**Die Gerätepfade dürfen den Cache noch nicht verwenden.** [RuntimeSettings](../../mower/runtime.py) lehnt `AZURE_TABLE` zusammen mit `PARK_ONLY`, `FULL_MOWER` oder `FULL_FAILSAFE` ab. Die drei direkten Geräte-Runner enthalten dieselbe Sperre, auch für bereits konstruierte Settings-Objekte. Diese Prüfung liegt vor deren Statusabruf und Befehlen.

Begründung: Neben der allgemeinen Hydrawise-Freigabekette existieren Bestätigungen für Fahrpläne, aufgehobene Zonenpausen und ein tatsächliches Zonenende. Mehrere davon zählen bislang Steuerungszyklen. Wiederverwendete Daten dürfen diese Ketten nicht künstlich fortsetzen. Die neu eingeführte Zeitkappe einer einzelnen Freigabeprüfung genügt für diese weiteren Ketten nicht.

A01 liefert daher eine getestete gemeinsame Lesekoordination für befehlsfreie Betriebsarten. Eine fertige Optimierung des produktiven FULL_FAILSAFE-Ablaufs ist damit nicht nachgewiesen. Das Aktivieren des Flags in der heutigen Gerätebetriebsart würde deren Ausführung ausdrücklich sperren und ist kein zulässiger Einführungsweg.

## Daten- und Funktionsvertrag

[status_cache.py](../../mower/status_cache.py) exportiert:

```python
read_status_cached(
    api_key, controller_id,
    environment=environment,
    hydrawise_config=hydrawise_config,
    now_utc=cycle_time,
) -> CachedStatusRead
```

| Feld | Bedeutung und Verwendung |
|---|---|
| `status` | Ausschließlich aktuell verwendbarer, validierter Inhalt; bei Fehler, laufender Abfrage oder abgelaufenem Quellstand `None`. |
| `last_known_status` | Letzter gespeicherter Inhalt zur Anzeige eines ausdrücklich alten Standes. Keine Freigabegrundlage. |
| `source_observed_at_utc` | Unveränderter Quellzeitpunkt aus der Statusantwort. Die relativen Zonenzeiten werden nicht auf die Uhrzeit des Lesers verschoben. |
| `fetched_at_utc` | Zeitpunkt des tatsächlich abgeschlossenen und erfolgreich gespeicherten GET. Cache-Treffer verändern ihn nicht. |
| `new_observation` | Nur beim eigenen erfolgreichen GET mit gegenüber dem letzten gespeicherten Stand fortgeschrittenem Quellzeitpunkt wahr. |
| `confirmation_observed_until_utc` | Bei brauchbarem Inhalt dessen ursprüngliches `fetched_at_utc`, sonst `None`. Grenze für zeitliche Bestätigungen. |
| `next_poll_at_utc` | Gemeinsamer frühester nächster Abruf; während einer ungeklärten Abfrage einschließlich Rückfallabstand. |
| `attempt_until_utc` | Ende des Beobachtungs-Lease von 60 Sekunden. |
| `quality` | Beispielsweise `FETCHED`, `CACHED`, `HTTP_429`, `SOURCE_STALE`, `SOURCE_NOT_ADVANCED`, `FETCH_IN_PROGRESS`, `FETCH_RECOVERY_BACKOFF` oder ein Speicherfehler. |
| `consecutive_errors`, `escalation_required` | Wiederholte Fehler beziehungsweise ungeklärte Abrüche. Ab drei Fehlern ist Aufmerksamkeit erforderlich; weitere GET bleiben budgetiert. |

`metadata()` liefert diese Diagnosefelder ohne Statuskörper. `now_utc` ist keine Methode zum Frischdatieren: Abruf- und Budgetzeit stammen aus der tatsächlichen Uhr. Tests verwenden eine ausdrücklich injizierte Uhr.

Bei `OFF` führt der Helper weder Speicherzugriffe noch GET aus; der aufrufende Code verwendet den bisherigen direkten Pfad. Bei ausgewähltem Cache und einem Speicher-/Konfigurationsfehler gibt es **keinen** stillen direkten Ersatzabruf.

[evaluate_continuous_clear_confirmation](../../mower/hydrawise.py) nimmt zusätzlich `confirmation_observed_until_utc` entgegen. Eine Zwei-Minuten-Bestätigung kann damit nicht allein durch zwei Minuten Wartezeit auf demselben Cacheeintrag erfüllt werden. Die physische Trocknungsfrist läuft weiterhin anhand der Wanduhr. Ein fehlender Beleg lässt sich durch `""` ausdrücklich als ungültig übergeben; `None` erhält den bisherigen Vertrag für unveränderte direkte Aufrufstellen.

## Gemeinsamer Speicher, Budget und Wiederherstellung

[status_cache_store.py](../../mower/status_cache_store.py) verwendet ausschließlich eine eigene Partition `ssv53-hydrawise-status-cache-v1` in der **bereits vorhandenen** Table. API-Schlüssel sind kein Bestandteil des Schlüssels: unterschiedliche Kontoschlüssel für dieselbe numerische Controllerkennung treffen dieselbe Cachezeile. Gespeichert wird ein Hash der normalisierten Kennung. Der Cache importiert keine `AutomationState`-Klasse und schreibt keine Steuerungszeile.

Die Auswahl `AZURE_TABLE` verlangt die vorhandenen Einstellungen `SSV53_STORAGE_ACCOUNT_URL`, `SSV53_STATE_TABLE_NAME` und die bestehende Managed Identity (`SSV53_STATE_MANAGED_IDENTITY_CLIENT_ID`, alternativ `AzureWebJobsStorage__clientId`). Es gibt weder Ressourcenerstellung noch zusätzliche Rollenvergabe. Azure-SDKs werden erst bei dieser Auswahl geladen; der ausgeschaltete eigenständige Plangenerator bleibt ohne Azure-SDK importierbar.

1. Der Leser lädt Inhalt und ETag. Ein gültiger gemeinsamer Abstand wird abgewartet, indem sofort der vorhandene Cachezustand zurückgegeben wird. Es gibt keine blockierende Warteschleife.
2. Nur ein erfolgreich bedingt schreibender Leser erhält eine neue zufällige Abrufkennung. Verlierer eines ETag-Konflikts laden den Zustand ihres Vorgängers; sie starten keinen zweiten GET.
3. Vor dem GET werden 60 Sekunden Lease und zunächst fünf Minuten zusätzlicher Rückfallabstand gespeichert. Damit kann ein Prozessabbruch keinen sofortigen Abrufsturm auslösen.
4. Ein Erfolg setzt das nächste Budget auf mindestens 60 Sekunden beziehungsweise den größeren Herstellerwert, gerechnet ab tatsächlichem Antwortempfang. Ungültige Zonen, alte oder rückwärts/gleich datierte Quellen erzeugen keinen neuen verwendbaren Stand. Ein trotzdem gültiger `nextpoll`-Wert wird auch bei solchen Fehlern eingehalten.
5. Fehler behalten Quellzeitpunkt, Abrufzeitpunkt und letzten Inhalt unverändert. Eine HTTP-Fehlerantwort macht den alten Inhalt selbst innerhalb seiner vorherigen Altersgrenze nicht zu einem aktuellen Erfolg. Der Rückfallabstand beträgt fünf Minuten, beim zweiten Fehler zehn Minuten, ab dem dritten Fehler eine Stunde; ein längeres `Retry-After` bleibt bindend. Ein erfolgreicher neuer Stand setzt den Fehlerzähler zurück.
6. Eine abgebrochene Abfrage kann nach Lease und Rückfallfrist mit einer neuen bedingt gespeicherten Kennung übernommen werden. Wiederholte ungeklärte Abbrüche erhöhen ebenfalls den Abstand. Es entsteht kein unbegrenzter Dauer-Latch für einen reinen Status-GET.
7. Vor dem Speichern einer Antwort werden aktuelle Abrufkennung und ETag erneut geprüft. Nach Verlust der Kennung kann eine verspätete Antwort weder Daten noch ein neueres Budget überschreiben. Ein nicht bestätigter Speicherabschluss veröffentlicht im betroffenen Aufruf keine frischen Daten. Ein zeitlich über 60 Sekunden ausgedehnter GET veröffentlicht ebenfalls keinen neuen verwendbaren Beobachtungsstand.

Die Requests verwenden einen Socket-Timeout von 20 Sekunden. Dieser begrenzt nicht zuverlässig die gesamte Lebensdauer einer langsam gelieferten HTTP-Antwort. Nach einem abgelaufenen Lease und Rückfallabstand können sich deshalb ein verspäteter alter und ein neuer **Lese-Request** technisch überschneiden. Die Datenübernahme bleibt durch Abrufkennung und ETag geschützt; eine absolute Nichtüberlappung aller Netzwerkpakete wird nicht behauptet. Das ist ein verbleibendes Rate-Limit-Risiko und kein verzögert wirksamer Gerätebefehl.

## Einbindung der Aufrufstellen

| Aufrufstelle | Entwicklungsstand |
|---|---|
| `dry_run.run_read_only_cycle` | Opt-in angebunden. Beibehaltung der echten Abrufzeit bei `record_cycle`; zusätzliche Zeitkappe bei der Freigabe. Ein regulärer DRY_RUN-Timer darf weiterhin seine ausdrücklich vorgesehene Beobachtungsprojektion speichern. |
| `platzwart_console.live_status` | Ruft beide Anzeigepfade mit `persist_observations=False` auf. Keine Änderung des Automationszustands durch eine App-Leseabfrage; gemeinsame Cachezeile bleibt getrennt. |
| `generate_schedule.main` | Opt-in angebunden, einschließlich validierter konfigurierter Relay-Liste und ursprünglicher Zeitangaben in `metadata.hydrawise_cache`. Bei unbekanntem Stand wird eine Warnung ausgegeben. Der vorhandene Dry-Run-Plan ist dann unvollständig und keine Gerätefreigabe. |
| `irrigation_recovery` | Helper vorbereitet und separat geprüft. Ein Cache-Treffer, ein Fehler oder ein unbestätigter Abrufzeitpunkt kann keinen Reset begründen. Der tatsächliche neue Abrufzeitpunkt bleibt erhalten. Der normale Einstieg ist wegen der FULL_FAILSAFE-Grenze bei aktiviertem Cache derzeit nicht erreichbar. |

Der eigenständige GitHub-Planworkflow besitzt bisher keine konfigurierte gemeinsame Table-Identität. Eine bloße Änderung des Azure-Flags koordiniert ihn daher nicht. Alle teilnehmenden Leser müssen denselben Cache, dieselbe Controllerkennung und kompatible Relay-Konfiguration verwenden; zusätzliche direkte Leser oder Hersteller-Apps liegen außerhalb dieser Zusicherung.

## Prüfung und Nachweise

Ausgeführt mit dem lokalen Python der Audit-Umgebung:

```powershell
& '../.audit-venv/Scripts/python.exe' -X utf8 -m pytest tests/test_status_cache.py tests/test_status_cache_readers.py tests/test_hydrawise_safety.py tests/test_irrigation_recovery.py tests/test_mower_schedule.py tests/test_cache_mode_gate.py tests/test_dry_run_hydrawise_hold.py -q
```

**89 Tests und 53 Subtests bestanden.** Alle Transport-, Geräte- und Azure-Zugriffe dieser Tests sind ersetzt; kein echter Status-GET, Gerätebefehl oder Deployment wurde ausgeführt.

Die [Cachetests](../../tests/test_status_cache.py) prüfen insbesondere unveränderte Quell-/Abrufzeiten, zwei gleichzeitig lesende Clients mit nur einem GET, `nextpoll`, beide `Retry-After`-Formate, defekte Inhalte, ausbleibenden Zeitfortschritt, Prozessabbruch, späteren Neustart, gleichzeitig konkurrierende Wiederherstellung, wiederholte Fehler und alte Antworten nach einem Besitzerwechsel. Ein Fake-Table-Client prüft getrennte Partition und bedingte ETag-Schreibzugriffe. Ein verlorener Speicherabschluss gibt keine unbestätigten Daten frei.

Die [Aufrufstellentests](../../tests/test_status_cache_readers.py) prüfen Plangenerator, vorbereitete Recovery, importierbaren OFF-Modus ohne Azure-SDK und den tatsächlichen `run_read_only_cycle`-Pfad. Dabei bleibt eine Zwei-Minuten-Kette trotz zwei Minuten auf demselben Cacheinhalt unbestätigt; eine bereits abgelaufene physische Frist bleibt erhalten. Bei `persist_observations=False` bleibt das gesamte zuvor gespeicherte Zustandsobjekt identisch. Die [Gatetests](../../tests/test_cache_mode_gate.py) prüfen die Sperre aller drei Gerätebetriebsarten auch bei direkten Runner-Aufrufen.

Das Gegenreview der neuen Runtime-/Anzeigegates fand keine Freigabeumgehung. Eine uneindeutige Statusbeschriftung für Cache-Treffer wurde an die Integration zurückgemeldet. Die vorhandenen Tests der bisherigen direkten Fehlerbereinigung und Planung bleiben erfolgreich.

## Impact, offene Nachweise und Einführung

| Aspekt | Bewertung |
|---|---|
| Herstelleraufrufe | Im gezielten Parallelitätstest werden zwei konkurrierende Leser durch einen GET bedient. Der Gewinn im realen Aufrufmix ist noch nicht gemessen. |
| Mähzeit | Kein nachgewiesener Gewinn, weil der produktive Gerätepfad ausdrücklich ausgeschlossen ist. Insbesondere keine behauptete Verkürzung von Trocknung oder Datenlückensperren. |
| Zuverlässigkeit | Gemeinsamer Abstand und eingezäunte Antwortübernahme; automatisches Wiederanlaufen nach budgetierter Frist. |
| Bewässerung/Sicherheit | Keine Geräteaktion und keine Änderung von Schutzzeiten, Akkuwerten, Bedarf oder Startregeln. |
| Betriebskosten | Keine neue Infrastruktur. Zusätzliche Lese- und bedingte Schreiboperationen in der vorhandenen Table; deren tatsächliche Zahl und Kosten sind noch zu messen. |

Vor einer befehlsfreien Erprobung bleiben nachzuweisen: tatsächlich gemeinsam erreichbare Table und bestehende Rollen, gemeinsamer Controller-/Relay-Vertrag, zeitlich ausreichend synchronisierte Hosts, eingebundene Aufrufstellen, tatsächlich installiertes Artefakt und eine brauchbare Reaktion auf `escalation_required`. Eine Metadatenmarkierung ist noch keine eingerichtete Alarmierung. Der GitHub-Workflow darf nicht mit einem unabhängigen direkten Abruf weiter eine gemeinsam behauptete Budgetgrenze umgehen.

Bei einer später gesondert freigegebenen Erprobung sind mindestens gleichzeitige App-/Timer-Leser, Neustart während GET, Cacheausfall, HTTP 429 mit Sperrfrist und Wiederherstellung sowie ein stale werdender Quellstand abzudecken. Abbruchkriterien sind unter anderem zusätzliche unkoordinierte GET, fortgeschriebene Abrufzeiten ohne neue Quelle, verlorene Sperrgründe und unerwartete Steuerungszustandsänderungen. Diese Ereignisse sind zunächst offline nachgewiesen, noch nicht im echten Parallelbetrieb beobachtet.

Ein Rückfall darf ein noch gespeichertes `next_poll_at_utc` oder `Retry-After` nicht durch einen sofortigen direkten GET umgehen. Die betroffenen Leser müssen erst bis zur spätesten bekannten Frist ruhen; dann kann nach kontrolliertem Stop der Cache-Leser und Ausschluss spät zurückkehrender alter Prozesse wieder der geprüfte direkte Lesepfad gestartet werden. Die Cachezeile während einer laufenden Reservierung nicht löschen. Da A01 keine Gerätebefehle sendet, entstehen daraus keine zusätzlichen Geräteaktionen, die zurückzunehmen wären; die bereits unabhängig laufende Gerätesteuerung und native Zeitpläne bleiben gesondert zu berücksichtigen.

Für eine künftige FULL_FAILSAFE-Anbindung sind sämtliche Bestätigungsketten auf voneinander unabhängige Quellbeobachtungen umzustellen, einschließlich Neustart, Cachefehler und ausgelassener Abfragen. Erst danach sind Gateänderung, Sicherheitsreview und gezielte menschliche Freigabe eines begrenzten Livepiloten vertretbar. Diese Erweiterung wurde hier bewusst nicht als erledigt ausgegeben.
