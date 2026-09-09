# Versions- und Betriebsnachweise

Erhebung am 09.09.2026, überwiegend 04:30–06:30 UTC. Zeitangaben ohne ausdrücklich
genannte Ortszeit sind UTC. Private Rohantworten verbleiben lokal außerhalb des
Git-Worktrees; veröffentlichbare Nachweise enthalten keine Schlüssel, Geräte-IDs,
E-Mail-Adressen oder Personenprofile.

**Ergänzung um 16:48 Uhr Berliner Zeit:** Der installierte Quellstand konnte
über den lesenden Azure-`admin/vfs`-Zugang geprüft werden. Alle 46 Dateien stimmen
bytegenau zum installierten Manifest, nach CRLF/LF-Normalisierung auch zum
Migrationscommit `9c2d0fc9c010b366cd49b58b8086b27f9d59a0dd`.
Die zuvor dokumentierte Zugriffslücke ist damit für Quellbytes geschlossen.
[Dateiweise Nachweise](installed-source-evidence.json). Dependencies, laufende
Zustände und Gerätewirkungen sind davon getrennt.

| Ebene | Tatsächlich ermittelt | Beweiskraft und Grenze |
|---|---|---|
| Heimspielrepository `main` | `d67e9d3fef24117f504a192c9ecaee998612ddd7` über expliziten Fetch und GitHub-API | Quellcode-/Datenstand; zunächst veralteter lokaler main-Ref wurde nicht übernommen. |
| Historischer Migrationsbranch | `feature/azure-mower-migration`, `9c2d0fc9c010b366cd49b58b8086b27f9d59a0dd` | Grundlage dieses technischen Audits. Nutzeränderungen im Originalcheckout erhalten. |
| Import-Hotfix | `efe932cbaf5186f050847a29347566782e891c20`, Branch `fix/cross-window-relocation-20260909`, [PR #46](https://github.com/Rohdeo87/ssv53-heimspiele/pull/46) | Eigener Main-PR; weder zusammengeführt noch als Feed veröffentlicht. |
| Gesamtaudit | Branch `audit/platzpflege-20260909`; Runtime-Codecommit `1b0fefa1cc9f2f2a5098de2c8bcc107a94bb0e71`; Zuordnung in [delivery.json](delivery.json) | Entwicklungsänderung, keine installierte Version. |
| Zweites Repository | `Rohdeo87/ssv53-app`, main `2d90d062d8c84d600eecd0dc592fe944963d08e5` | RN/Expo-Prototyp, keine belegte produktive Steuerungsintegration. |
| Azure-Infrastruktur | RG `rg-ssv53-platzpflege-prod`; App `func-ssv53platzpflege-prod-q7kbw54s`; Running, HTTPS-only; Flex Consumption/Python 3.12, 512 MB, maximal 40 Instanzen | Ressourcen-/Konfigurationsnachweis; Instanzobergrenze beweist keine 40 gleichzeitig laufenden Timer. |
| Deploymentdatensatz | Aktiv, Status 4, ID `cbac58e0-76b7-4fb2-b789-c9332d885b57`, Eingang 06.09., 05:35:20.675, Ende 05:36:37.232 | Beweist abgeschlossene Plattformbereitstellung, nicht identische Git-/Buildbytes. |
| Installierte Quelldateien | Zunächst Blob-/SCM-Zugriff gescheitert; um 16:48 Uhr alle 46 Quellen und Manifest über Azure admin/vfs gelesen | 46/46 Manifest-Hashes bestätigt, 46/46 zum Migrationscommit nach Zeilenenden-Normalisierung; Dependency-Wheels und Gerätewirkung nicht dadurch nachgewiesen. Keine Rechteänderung. |
| Appack-CMS | Repositorytemplates zeigen auf den genannten Azure-Host | Aktuell gespeicherte/veröffentlichte/auf Geräten geladene Templateversion nicht direkt zugänglich. Lokale Screenshots sind keine CMS- oder Geräteabnahme. |
| Modell und beobachteter Zustand | Log vom 09.09., 05:48: Automower 580 EPOS, 95 % Akku, ERROR/Code 93, kein bestätigtes Dock; Wasserablauf wartet | API-Telemetrie, keine physische Stations-/Ventilprüfung. |

Offene PRs bei Beginn: Heimspielrepo #43 (lxml), #29 (setup-python), #28
(checkout), #27 (download-artifact), #25 (github-script); Apprepo #1/#2
(Actions-Abhängigkeiten). Keine davon wurde geändert oder zusammengeführt.
Die Audit-PRs sind zusätzliche, bewusst getrennte Vorschläge.

Das [lokal gebaute Quellpaket](package-evidence.json) enthält 47 Dateien inklusive
Manifest. Die enthaltenen Dateihashes wurden gegen das Manifest geprüft. Es
benötigt noch einen Azure-Remote-Build und wurde weder veröffentlicht noch
installiert. Künftige Zykluslogs enthalten `build_provenance` mit dem Bytehash
des geladenen Entrypoints und, falls vorhanden, des Paketmanifests. Das Feld
`all_installed_files_verified=false` macht ausdrücklich kenntlich, dass diese
Diagnose allein keine vollständige Prüfung aller installierten Dateien ist.

Zusätzlich erzeugte der [GitHub-Paketworkflow](https://github.com/Rohdeo87/ssv53-heimspiele/actions/runs/34321434490)
erfolgreich ein **separates Read-only-Quellpaket** mit 39 Dateien. Dessen
[Artefakt](https://github.com/Rohdeo87/ssv53-heimspiele/actions/runs/34321434490/artifacts/10092007318)
und [Metadaten-/Lognachweis](ci-package-evidence.json) sind vorhanden; die
Aufbewahrung endet am 23.09.2026. Die entfernten ZIP-Bytes wurden nicht erneut
heruntergeladen. Dieses Artefakt ist weder das lokale 47-Dateien-FULL_FAILSAFE-
Paket noch ein installierter Azure-Remote-Build. Beide Prüfungen senden keine
Gerätebefehle.

## Verifizierte Konfiguration

| Funktion | Live gelesene Werte / Codevertrag | Bedeutung |
|---|---|---|
| Betriebsstufe | `CONTROL_MODE=FULL_FAILSAFE`; Live-Reads, Park-, Start- und Irrigation-Schreibflags aktiv | Bestehende Produktion ist bereits schreibend; der Audit selbst hat keine Geräteaktion ausgelöst. |
| Neue/adaptive Planung | `ADAPTIVE_PLANNING_ENABLED=true`, `ADAPTIVE_EXECUTION_ENABLED=false`; Wetter aktiviert, Shadow | Vorhandener Schattenvorschlag darf nicht als neue beobachtete Optimierung ausgegeben werden. |
| Quellen | Dynamic Config aktiv, `CONFIG_MAX_AGE_MINUTES=1440`; Sicherheitsbundle strenger geprüft | Anzeige und Steuerung können bewusst verschiedene Verfügbarkeitsgrenzen haben; dieselbe Quelle, kein erfundener Aktualitätsnachweis. |
| Wasser | sieben erwartete Zonen; `POST_IRRIGATION_DRYING_MINUTES=150`, `HYDRAWISE_CLEAR_CONFIRMATION_MINUTES=150`, Gap-Bestätigung 2 Minuten, Full-Mower-Bestätigung 10 Minuten | 150 ist physische Projektfrist; 2/10 sind Datenvertrauen je Modus. |
| Rückkehr | Parkvorlauf 4 Minuten, zwei Bestätigungszyklen, Parkbestätigung mindestens 1 Minute, Fortschrittsgrace 3 Minuten, Statusalter max. 180 Sekunden | Beides sind Voraussetzungen; angenommener Parkbefehl ersetzt keine Dockmeldung. |
| Akku | Fortsetzung 60 %, Neustart 90 % | Hersteller kann bei Start dennoch volle Ladung abwarten; keine lineare Prozent-pro-Minute-Prognose. |
| Wasserablauf | Capture-Vorlauf max. 45 Minuten; Docklead 40; Planänderung 2, Lease 3, Startbestätigung 5, Zonenende 2 Minuten; Suspendmargin 60; frühes Ende Toleranz 120 Sekunden | Vorsichtige Sequenz- und Ausfallgrenzen, keine im Audit verkürzte Zeit. |
| Zeitfenster | Mäherrepository ganztägig; Wasser-Shadow 01:00–07:30, Ziel 04:30, Ende bis Sonnenaufgang + 60; Import 06–22 | Unterschiedliche Funktionen; keine pauschale Nacht-Mähsperre aus dem Importfenster ableiten. |

## Konkrete Fehlerketten aus Produktion

**Import:** [Lauf 34312723317](https://github.com/Rohdeo87/ssv53-heimspiele/actions/runs/34312723317),
09.09., 04:57:03: ausdrückliches Verlegungsziel fehlt im zuerst gelesenen Fenster.
Letzter guter Quellenstand 08.09., 04:51:22 bleibt stehen. Das
[Runtime-Bundle 34283743125](https://github.com/Rohdeo87/ssv53-heimspiele/actions/runs/34283743125)
scheitert an 720 Minuten Höchstalter. Das letzte hier nachgewiesene erfolgreiche
Bundle ist [34225267114](https://github.com/Rohdeo87/ssv53-heimspiele/actions/runs/34225267114).
Die öffentliche Belegungsabfrage für 09.–17.09. liefert am 09.09., 05:54 HTTP 500;
der zugehörige Trace meldet die überschrittene Quellenaltersgrenze. Das ist eine
belegte Folge des alten Imports, kein Nachweis eines Defekts im Blobcache.

**Trocknung und Datenlücke:** 08.09., 05:53: Ablauf vollständig gemeldet,
`COMPLETE_HOLD`, nominelles Ende der alten 150-Minuten-Frist 08:23. 08:07–08:09:
drei fehlende Hydrawise-Abfragen löschen die kontinuierliche Uhr. 08:10: neuer
Beginn, neue Frist 10:40. Der Abstand zwischen gültigen Abfragen beträgt etwa
vier Minuten und überschreitet 180 Sekunden. Die Verlängerung um 137 Minuten ist
beobachtet, aber **nicht als vermeidbar nachgewiesen**: Eine dazwischen erfolgte
Bewässerung ist nicht ausgeschlossen. Der neue konservative Code hält auch
hier erneut 150 Minuten. Die vermeidbare Vollverlängerung nach nur einer kurzen
Abfragelücke ist separat in Tests reproduziert, nicht als realer Gewinn gezählt.
[Minimierter Ereignisauszug](observed-incidents.json)

**Bewegung während Sperre:** 08.09., 08:11 wird `MOWING` gemeldet und unmittelbar
ein Schutzparkbefehl protokolliert; 08:12 Heimfahrt, 08:13 Station. Um 10:22 wird
erneut `LEAVING` mit Schutzparken gemeldet. Die Sperre bestand noch. Ursache
(Benutzeraktion, native Planung, verspäteter Befehl) ist aus diesem Export nicht
bestimmbar. Dies ist eine kritische Telemetrieabweichung, kein belegter
Personenschaden und kein Beweis, welcher Scheduler sie ausgelöst hat.

**EPOS:** Mehrere Fehler-93-Episoden sind sichtbar, z. B. 06.09., 05:44–07:58 sowie
erneut 09.09., 05:19–05:51. Die abgegrenzte Baseline enthält 609,01 Minuten Code 93.
Laut Hersteller steht 93 für fehlende genaue Satellitenposition. Antennenlage,
Sichtfeld, Referenzstation/Korrekturdaten und Firmware sind vor Ort zu prüfen;
eine reine App-/Pufferänderung behebt diese Ursache nicht.
[Husqvarna Fehlerbeschreibung](https://docs.developer.husqvarnagroup.cloud/automower-connect-api/status-description-and-error-codes.md)

## Ausgangsmessung

Intervall: 02.09., 06:00–09.09., 05:00 UTC, 10.020 Minuten. Aus dem größeren lokalen
Export fallen 10.016 Beobachtungen in diesen Abschnitt; Zeitabdeckung 99,9 %,
10,07 Minuten ohne Beobachtung. `NOT_APPLICABLE` wird nicht als Parken geraten.
[Reproduzierbare Baseline](operating-baseline.json),
[Auswerteskript](../../scripts/analyze_control_observations.py)

| Beobachtete Klasse | Minuten, gerundet | Interpretation |
|---|---:|---|
| MOWING ohne Fehler | 2.870,59 | Produktive Telemetrie-Schätzung, kein Schnitt-/Flächenqualitätsmesser |
| Heimfahrt | 97,80 | Getrennt von Schnittzeit |
| Fahrt zum Platz | 96,25 | Getrennt von Schnittzeit |
| Laden | 478,02 | Physischer API-Zustand, kein verlorenes Mähfenster |
| Geparkt | 4.284,42 | Kann notwendige oder zusätzliche Sperren enthalten |
| Gerätestörung | 691,02 | Darunter 609,01 Minuten Code 93 |
| Nicht klassifizierbarer Zustand | 1.491,82 | Keine automatische Zuordnung zu vermeidbarer Wartezeit |

362,99 Minuten wurden mit mindestens einer aktiv gemeldeten Bewässerungszone
beobachtet; diese können Laden/Sperren überlappen und werden **nicht** zur
Stillstandsbilanz addiert. 145 gemeldete Befehlsversuche sind kein
Ausführungsnachweis. Echte Wassermenge, erfüllter Bedarf, vermeidbare
Sperrminuten und Ausnutzung geeigneter Schnittfenster bleiben mangels
vollständiger Bedarfs-/Sperr-/Durchflussdaten `null`. Benötigte Nachmessung steht
im Einführungsplan. Die Baseline umfasst verschiedene historische
Deployments; sie ist kein kontrollierter A/B-Versuch.

## Letzter Git-Abgleich der Import-PR

Der [lesende Merge-Tree-Nachweis](import-pr-current-merge-review.json) vergleicht
PR 46 zusätzlich mit dem inzwischen weitergelaufenen Main. Die einzige neue
Main-Datei ist `state/request_state.json`; lokal ist kein Inhaltskonflikt
reproduzierbar. Das abweichende Connector-Signal wird separat aufgezeichnet
und vor einem später genehmigten Merge erneut direkt geprüft. Es wurde kein
Merge oder Branch-Rebase ausgeführt.
