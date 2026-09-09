# Gestufte Einführung und Betrieb

Dies ist ein vorbereiteter Ablauf. Die inzwischen erteilte bedingte Livefreigabe und ihre noch nicht erfüllten Voraussetzungen stehen im [aktuellen Prüfstand](../ui-2026-09-09/release-readiness.md). Aktuell wurden
Analyse, Entwicklung, lokale Tests, Simulation und lesende Betriebsprüfung
durchgeführt. Der neue Planer hat keine Geräteanbindung. Keine der folgenden
Liveaktionen wurde während des Audits ausgeführt.

## Abnahmestufen

| Stufe | Maßnahmen und Ausschlüsse | Voraussetzungen | Abnahme und Ereignisabdeckung | Erfolgsmessung | Abbruch | Rückfall | Menschliche Freigabe |
|---|---|---|---|---|---|---|---|
| 1 Bestandsaufnahme | Git/Workflows/Azure/Logs/Quelle inventarisieren; keine Mutationen an Produktion | Vorhandene lesende Zugänge | Ausgangsbaseline und Versionsmatrix vorhanden; installierte Bytes, Geräteschedules, Station und Mannschaftsliste ausdrücklich offen | Datenabdeckung, klare Unbekannte, reproduzierbarer Importfehler | Fehlende Identität des Zielsystems oder unkontrollierte Aktion | Nur lesend weiterarbeiten; private Rohdaten lokal halten | Keine zusätzliche Freigabe für bereits beauftragte Analyse |
| 2 Fehler und Diagnose | Importfix, getrennte Trocknungsfrist, strikte Statusprüfung, START-Reservierung, ETag und Messfehler beheben; kein Schwellwerttuning | Erklärte Ursache, Regression und unabhängiger Review | Alle relevanten Tests und CI am exakten Kopf; Gegenvergleich mit Basiscode; Einzelpoll, externe Bewässerung, 240-s-Lücke, Neustart, zwei Writer, verlorene Antwort | Fehlerfälle reproduzierbar beseitigt; notwendige Sperre bleibt | Neue Freigabe bei ungültigen Daten, Testregression, unklare Migration | Entwicklungsänderung korrigieren; Produktionsrollback ist separat gerätebewusst | PRs zur Prüfung beauftragt; Merge/Veröffentlichung gesondert |
| 3 Zustände und Bedienung | Gemeinsame lesende Releaseprüfung, Mehrfachsperren, Datenalter, HMAC-Schreibgrenze, unbekannte Ladezeit; keine Trainer-Identität erfinden | Authentifizierter Ersatzpflegeweg; Backend-/Templatevertrag | FULL_FAILSAFE-Status ohne State-Schreiben getestet; Desktop/390 px/Ausfall geprüft; zusätzlich reales Appack-WebView, Sitzungswechsel, Rollen und Ablauf testen | Keine widersprüchliche Freigabe, keine Schreibwirkung ohne Sitzung | UI sagt frei bei fehlenden Daten; berechtigter Ersatzpflegeweg funktioniert nicht | Backendauth erhalten; Ersatzpflege durch Platzwart; alte anonyme Writes nicht wieder aktivieren | Rollenwirkung und abgestimmte CMS-/Backendveröffentlichung freigeben |
| 4 Gemeinsame Simulation | Bestand als Modell, einfache Laderegel, vorausschauender Vergleich; stabiles Bedarfsjournal; keine Geräteaktion | Vollständige Modellannahmen, gleiche Wasser-/Energiebilanz | 29 Koordinationstests, Zeitumstellungen, Vorziehen+Neustart, Originaltermin, Stop, Unsicherheit; Sensitivität dokumentiert | Produktive Minuten und vereinigte Sperren; gleiches Wasser und gleiche Restenergie | Optimierung reduziert Bedarf, verschiebt Sport oder erlaubt unsicheren Start | Basismodell; Optimierer bleibt deaktiviert | Keine weitere Freigabe für Offline-Modell |
| 5 Parallelbeobachtung | Neue Vorschläge aus datierten Kopien realer Eingänge berechnen und mit Bestand vergleichen; kein Gerätesenden, keine Änderung nativer Pläne | Versionierter Read-only-Adapter und eindeutige Bedarfsfenster; die neue Erfassung kopiert vorhandene Eingänge ohne zusätzliche Herstellerabfrage, zusätzliche Leser benötigen ein abgestimmtes Budget; vorher Test, dass Sender nicht aufrufbar sind | Vorschlag: mindestens 14 Tage **und** mindestens 5 Bewässerungen, 10 Ladezyklen, 2 Spiel-/Trainingswechsel sowie beobachteter/gezielt offline injizierter Ausfall. Keine Stufe nur wegen Zeitablauf bestehen lassen | Gewinne nur bei gleichen Eingangsdaten; Bedarfserfüllung, unbekannte Minuten, Konflikte und Planwechselquote | Unerklärter Vorschlag bei belegtem Platz, veraltete Daten, zusätzlicher API-Fehler oder Wasserbedarf ohne Herkunft | Read-only-Adapter stoppen; Bestand unverändert; aus Vorschlag keine Aktion ableiten | Separater befehlsfreier Betriebsversuch und ggf. Log-/API-Zugang freigeben |
| 6 Begrenzter Livepilot | Eine vorab bestimmte Bedarfsinstanz in engem freigegebenem Fenster vorziehen; persistenten Verbraucher gezielt prüfen; kein unbewachter Nachtbetrieb, kein allgemeiner Dispatcher-Rollout | R1/R2/R8/R13 geschlossen; Ursache der Sperrbewegung geklärt; nativer Plan exportiert; Queue-/Dock-/Weg-/Ventiltest; Schattenabnahme | Vorschlag: 3 beaufsichtigte vollständige Läufe mit Zonenende, Trocknung, Originalterminunterdrückung und Folgeladung; zusätzlicher beaufsichtigter Kommunikations-/Neustartversuch | Reale MOWING-Minuten, Rückkehrzeiten, Wasserbedarf unverändert erfüllt, keine Doppelbewässerung | Jede unbekannte Startwirkung, verspätete Rückkehr, unerwartete Zone/Bewegung, widersprüchlicher Plan, fehlende Aufsicht | Lokale Sicherung; ausstehende Aktionen und nativen Plan abgleichen; erst dann Code/State zurücksetzen | Konkreten Ablauf, Zeitraum, verantwortliche Person, Stopmöglichkeit und Rückfall bestätigen |
| 7 Erweiterung und Nachkontrolle | Schrittweise weitere Bedarfstermine/Zeiten; keine automatische Freigabe weiterer Plätze oder kürzerer Trockenzeit | Pilotnachweis und stabile Bedienung; fachlich bestätigte Bedarfskorridore | Vorschlag: je Erweiterung 14 Tage und relevante Ereignisabdeckung; Saisonwechsel/DST weiter in Tests, später real beobachten | Produktive Zeit relativ zu geeigneten Fenstern, gleiche Rasen-/Wasserversorgung, API-/Betriebskosten | Sicherheitsabweichung, steigende unklare Befehle, verpasster Bedarf, Rasenbeeinträchtigung | Umfang auf letzte nachgewiesene Stufe begrenzen; Geräteaktionen mit zurückführen | Jede Erweiterung gesondert abnehmen |

Die genannten Beobachtungsdauern sind vorgeschlagene Abnahmeregeln, keine
statistische Sicherheitsgarantie. Fehlerfreie drei Pilotläufe belegen keine
allgemeine Fehlerfreiheit.

## Verbindliche Invarianten

1. Fehlende/alte/unvollständige Daten erzeugen keine automatische Freigabe.
2. Sport- und verbindliche Platzsperren samt bereits angewendeten Puffern werden
   nicht vom Optimierer verschoben oder verkürzt.
3. Vor einem konfliktträchtigen Wasserstart sind frische Stationsbestätigung,
   vollständige erlaubte Zonen, sichere Station/Fahrwege und verhinderter
   autonomer Mäherstart erforderlich.
4. Ein ausdrücklich gesetzter Stopp bleibt über Neuplanung und Neustart bestehen.
5. Eine unklare START-Wirkung ist keine abgelaufene Lease. Weder Zeitablauf noch
   spätere Dockmeldung beweisen eine leere Herstellerwarteschlange.
6. Ein akzeptierter API-Befehl ist kein physisch bestätigter Zustand. Aus einer
   relativen Befehlsdauer lässt sich ohne Queueverhalten keine harte physische
   Ankunftsfrist ableiten.
7. Ein Ladeereignis erzeugt keinen zusätzlichen Wasserbedarf. Ein Vorziehen
   benötigt dieselbe stabile Bedarfsinstanz und nachgewiesene Unterdrückung des
   ursprünglichen Termins.
8. Eine Appabfrage erklärt den Zustand und schreibt keinen Steuerzustand. Der
   Controller prüft unmittelbar vor Reservierung und Sendung erneut.

## Fehlermatrix und Auflösung

| Fehler | Sicheres Verhalten / Entscheidung | Begrenzte Wiederherstellung | Eskalation und Auflösungsbedingung |
|---|---|---|---|
| Einzelner Hydrawise-Poll fehlt | Aktuell unbekannt, kein Start; physisches `dry_until` bleibt bestehen | Nächster zulässiger Poll; danach bestehende Datenbestätigung, keine volle neue Trockenfrist bei Lücke innerhalb von 180 s ohne bekannten möglichen Lauf | Wiederholte Fehler zählen; Freigabe erst nach beiden Nachweisen |
| Hydrawise-Lücke > 180 s oder über bekannten Start | Mögliche unbeobachtete Bewässerung; konservative neue 150 Minuten ab sicherer Wiederkehr | Begrenzter Backoff gemäß Hersteller; keine parallelen aggressiven App-Retries | Lücke/Grund anzeigen; bei anhaltendem Ausfall Platzwart und tatsächlichen Ventilzustand prüfen |
| Import fehlerhaft/leer/teilweise | Letzter gültiger Stand bleibt; Rücknahme nicht ungeprüft; Quelle/Zeiträume als unzuverlässig | Geplanter budgetierter Abruf, keine Schleife gegen Challenge; bestätigte neue Sperren manuell ergänzen | Überschreiten von 720/1440 Minuten konkret melden. Rückkehr nur mit vollständigem validem Import; fehlende Mannschaften manuell klären |
| Mäher erreicht Dock nicht | Kein Wasserstart; Parkanforderung nicht als Station zählen | Bestehende Zeitgrenzen und begrenzte Wiederholungen; Rückkehrfristen vor Belegung überwachen | Bei nächster Belegung oder Fehler sofort zuständiger Platzwart; Gerät lokal sichern. Keine automatische Bewässerung als „Nachholen“ |
| START-Antwort fehlt / Prozess stirbt nach Sendung | Persistenter Pending-Latch; keine weiteren START/Wasser/Resume-Befehle | Nach 90 s gegebenenfalls gesondert reserviertes Schutzparken mit Dedupe; selbst dessen Erfolg ist keine Queueleerung | Expliziter Abgleich von Vendorqueue, nativem Plan und lokalem Parkzustand. Danach revisionsgebundene manuelle Recovery; kein gewöhnlicher Startbutton zum Löschen |
| Zwei Controller schreiben | CAS-Konflikt sendet im reservierten Pfad keinen START; keine blinde Wiederholung | Neuer Zyklus lädt aktuellen Zustand | Alle anderen Hosts/Adapter/native Pläne identifizieren. Gemeinsame Zeile allein reicht gegen fremde Sender nicht |
| Mäher offline / EPOS-Fehler | Keine automatische Freigabe, Dockbeweis fehlt | Verbindung/Korrekturdaten wiederherstellen; Fehlerfreiheit frisch beobachten | Gerät vor Ort prüfen; Code 93 ist keine Akkuschwelle und kein Anlass Schutzpuffer zu verkürzen |
| API-/Tokenfehler | Unbekannte Zustände; keine Annahme „alle Ventile zu“ | 401/403 mit Konfigurationsverantwortlichem lösen, 429 gemäß nextpoll/Retry-After; keine Schlüssel in Logs | Erfolgreiche aktuelle Antwort und vollständige Zone-/Geräteprüfung |
| Cloudsteuerung fällt bei laufendem Gerät aus | Zentraler Sender kann keinen lokalen sicheren Zustand garantieren | Bestehende begrenzte Geräteaktion kann weiterlaufen; native Zeitpläne können übernehmen | Aufsicht/lokaler Stopp erforderlich. Timeralarm ist ein Hinweis, keine Sicherheitsfunktion |
| Neues/abgesagtes/verlegtes Spiel | Belegung neu planen; Entfernung/Verkürzung nur nach bestätigter Rücknahme | Nach Quellenvergleich aktualisieren, feste Sportzeiten nicht optimieren | Bei kurzfristigem Ereignis bestätigte Ersatzsperre; Freigabe nicht aus leerer Anzeige ableiten |
| UI-/Sitzungsfehler | Letzten Stand deutlich kennzeichnen, Bedienung sperren | Aktualisieren bzw. erneut anmelden | Bei wiederholtem Fehler berechtigter Ersatzpflegeweg; keine anonyme Freigabe |

Eskalationen sind Betriebsanweisungen. Im Audit wurden keine Mails/Nachrichten
an Dritte gesendet und keine Alarmempfänger geändert. Bestehende vier Azure-
Alarmregeln sind vorhanden; Zustellung, Bereitschaft und lokale Reaktionszeit
sind noch praktisch zu prüfen. Die uncommitteten Alarmänderungen des Nutzers
wurden nicht übernommen.

## Rückfall mit Geräteabgleich

Vor jedem Code-Rollback wird zunächst festgestellt, ob ein Mäherstart, ein
Zonenstart, eine Suspendierung, eine Terminänderung oder eine Bedienanforderung
angenommen, bestätigt oder ungeklärt ist. Alle aktiven Sender müssen in einem
abgestimmten Ablauf an weiteren Starts gehindert werden. Das ist für Produktion
freigabepflichtig und wurde hier nicht durchgeführt.

Die lokale Sicherung durch einen Verantwortlichen geht einem Datenbankreset
vor. Danach Geräteprogramme, Laufrest, Queue und Suspendierungsendzeiten mit
dem Journal vergleichen. Kein dokumentierter globaler Queue-Löschbefehl wird
unterstellt. Kann die Queue nicht sicher abgeglichen werden, bleibt das Gerät
lokal gesichert; ein weiteres API-Parkkommando ist kein Beweis einer endgültigen
Aufhebung älterer Befehle.

Vorherigen Code, aktuelle Statezeile einschließlich ETag/Revision, Wasserjournal
und aktuelle Gerätepläne sichern. Die alte Fassung kennt
`mower_start_pending_since_utc`/`mower_start_pending_deadline_utc` und
`hydrawise_drying_since_utc` nicht. Sie darf nicht unkontrolliert über diese
Zustände schreiben. Erst nach bestätigter Auflösung aller ausstehenden
Wirkungen und abgestimmter State-Migration darf der frühere Sender zurückkehren.
Ein offener Wasserbedarf muss erhalten bleiben, statt versehentlich auszufallen
oder erneut vollständig bewässert zu werden.

Für den Import-Hotfix ist der Rückfall getrennt: ein Code-Revert darf bereits
bestätigte neue Platzsperren nicht still entfernen. Letzte valide Daten und
Ersatzsperren erhalten, Quellenproblem erneut bewerten, Rücknahmewächter aktiv
lassen. Ein Quellenupdate ist kein Controller-Software-Rollout.

## Zwei gestufte Freigabeentscheidungen

Nachtrag 09.09.: Die folgende getrennte Einführungsfolge beschreibt den vorbereiteten technischen Ablauf. Die aktuelle Nutzerzustimmung ist an die vollständige Umsetzung gebunden; sie erlaubt derzeit keinen vorgezogenen Teilrollout. Der aktuelle Stand enthält den persistenten Verbraucher und die gemeinsame Trainingslaufzeitquelle, beide standardmäßig deaktiviert.

**Entscheidung 1 – Preflight:** Nachweise für den konkret vorgeschlagenen Import-/Trainings-/Paketstand prüfen, einschließlich exaktem Commit, Pflichtchecks, Bundle-Vergleich, Standardflags und Rückfallplan. Diese Entscheidung aktiviert keine Geräteoption und veröffentlicht nichts.

**Entscheidung 2 – Aktivierung:** Erst nach dokumentierter Preflight-Entscheidung die ausdrücklich benannten produktiven Änderungen und gegebenenfalls den eng begrenzten, beaufsichtigten Pilot freigeben. Installation, echte Appack-Sitzung und physische Gerätewirksamkeit können erst danach als gestufte Post-Aktivierungsprüfungen beobachtet werden; sie sind kein vorab erfüllbarer Paketnachweis.

Für den Preflight [PR #46](https://github.com/Rohdeo87/ssv53-heimspiele/pull/46)
am dann tatsächlich vorliegenden exakten Kopf prüfen und den **Import-Merge
mit kontrolliertem anschließendem Quellenlauf** vorbereiten. Der
[CI-Lauf 34318897870](https://github.com/Rohdeo87/ssv53-heimspiele/actions/runs/34318897870)
war erfolgreich. Vor tatsächlichem Merge erneut Kopf, Basis, erforderliche
Checks und Konfliktfreiheit prüfen. Danach aktuelles Saisonergebnis und die
Rasenbelegung am 08.10., 17:00–20:25 Uhr in JSON, ICS, Runtime-Manifest und API nachweisen.
Keine erwartete Datenzahl blind erzwingen, falls sich die Quelle inzwischen
fachlich geändert hat.

Die technische Audit-PR benötigt eine eigene Review-/Releaseentscheidung.
Nach der Preflight-Entscheidung sind Rollenwirkung, State-Migration und
Rückfall für die Aktivierung festzulegen. Die anschließenden Post-Aktivierungs-
prüfungen umfassen installierte Versionskette, native Gerätepläne, echte
Appack-Sitzung und beaufsichtigten Gerätepilot. Eine Freigabe des Importfixes
genehmigt keinen Mäher-/Bewässerungspilot und keine Veröffentlichung der
Appack-Templates. Der persistente Optimierer bleibt bis zur ausdrücklichen
Aktivierungsentscheidung standardmäßig befehlsfrei.
