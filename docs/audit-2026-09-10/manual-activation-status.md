# Aktivierung am 10.09.2026

Auf ausdrücklichen Auftrag „Bitte aktivieren“ wurden um 11:42 Uhr die
Vollautomatik, die bestätigte manuelle Bedienung und die Befehlsfreigaben
aktiviert. Schutzregeln, übrige Einstellungen und gespeicherte manuelle
Sperren wurden nicht verändert.

Der laufende Dienst meldet ab 11:44 Uhr `FULL_FAILSAFE` mit dem zuvor
installierten und geprüften Manifest. Beim ersten beobachteten Zyklus wurde
wegen einer bestehenden Bediener-Parksperre und erneuter Ausfahrt ein
Schutzparkbefehl gesendet. Danach meldete der Mäher `HOME`; um 11:46 Uhr
lautete die Aktivität `GOING_HOME`, Fehlercode 0. Die Parksperre bleibt bis zur
bestätigten Freigabe bestehen. Es wurde kein Mäherstart erzwungen.

Alle sieben Bewässerungszonen wurden frisch und frei gemeldet. Ein realer
Bewässerungsablauf und der vollständige anschließende Trocknungs-/Ladezyklus
sind in dieser Aktivierung nicht nachgewiesen. Die Rückfahrt ist eine
Herstellermeldung, keine Sichtprüfung vor Ort.

Die optionale Bündelung von Laden und Bewässerung hat jetzt die technische
Freigabe, bleibt aber durch `APPROVED_COORDINATION_CONFIG_DISABLED` blockiert:
Es fehlt die freigegebene Bedarfsplanung. Es wurde kein Bedarf erfunden und
keine zusätzliche Bewässerung veranlasst. Die reguläre sichere Steuerung ist
hiervon getrennt aktiv.

[Schalter und beobachtete Steuerungszyklen](manual-activation-proof.json).
Software und CMS stammen aus den geprüften PRs 59 und 60; diese Dokumentation
ändert keinen Code und keine weitere Betriebseinstellung.

Die anschließenden Herstellerbeobachtungen um 11:47 und 11:48 Uhr melden
`PARKED_IN_CS`, `HOME`, Akku 100 %, Fehlercode 0 und keine aktive Wasserzone.
Die Bediener-Parksperre bleibt gespeichert; keine weiteren Befehle wurden
in diesen beiden Zyklen gesendet. Siehe `followup_observations` im Nachweis.
