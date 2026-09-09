# Offline-Entwurf für eine bestätigte Bewässerungsinstanz

Der neue `mower/coordination_request.py`-Adapter prüft einen vorhandenen
`SHADOW_PROPOSAL` mit exakt denselben `cycle`, `need` und Lade-Schätzdaten erneut.
Bei abweichendem Eingabehash, veraltetem Plan, fehlendem Zustand, Dockbeleg oder
manuellem Stopp entsteht ausschließlich `BLOCKED`.

Ein gültiger Entwurf erzeugt eine deterministische `request_id` aus `need_id`,
dem gebundenen Quellenplan, dem gewählten Start und der vollständigen
Zonenfolge. Die Ausgabe bewahrt Reihenfolge, relative Startabstände und Dauer;
Wassermenge und 150 Minuten Trocknung bleiben unverändert. Sie enthält keine
Geräteaktion und setzt stets `permission_to_start=false` sowie
`execution_available=false`.

Die bestehende Verbrauchergrenze von mindestens 45 Minuten Vorlauf wird vor
der Entwurfserzeugung erneut geprüft; 45 Minuten sind zulässig, 44 Minuten
führen zu `EXISTING_CONSUMER_MINIMUM_LEAD`. Der Bedarf muss außerdem die explizite
`source_plan_id` (über `canonical_schedule_id()` gebildet) enthalten. Ablauf,
Planänderung oder fehlerhafte Zeitwerte führen zu `BLOCKED`.

Dies ist kein Liveadapter. Der bestehende Operatorpfad benötigt weiterhin einen
separat geprüften Consumer, der explizite Abstände, Quellenplanbindung,
persistente Need-Deduplizierung, Unterdrückungsbestätigung und die bestehenden
Mäher-/Wasser-Sicherheitsübergänge korrekt verarbeitet. Der Entwurf ruft weder
Runtime-Validatoren noch Hydrawise- oder Mäher-Sender auf.
