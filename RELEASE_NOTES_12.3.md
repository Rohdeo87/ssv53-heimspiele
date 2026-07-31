# Stable Release 12.3

Version 12.3 erweitert den stabilen Abrufstand 12.2 um eine direkt auf dem Handy lesbare Änderungskontrolle und automatische GitHub-Benachrichtigungen.

## Neuerungen

- Workflow-Summary für neue, geänderte und entfernte Platzspiele
- Schutz vor leerem Folgefeed und massivem unplausiblem Spielverlust
- sichere Testläufe auf Entwicklungsbranches
- GitHub-Issues bei echtem Handlungsbedarf
- keine doppelten identischen Warnungen
- automatische Schließung technischer Warnungen nach erfolgreicher Erholung
- keine Benachrichtigung bei unverändertem erfolgreichem Abruf

## Unveränderte Schutzgrenzen

- höchstens zehn Requests pro Lauf
- mindestens drei Sekunden Abstand
- höchstens ein Retry bei Timeout oder HTTP 502/503/504
- sofortiger Stopp bei HTTP 429
- dauerhafte Sperre bei HTTP 403, 406 oder erkannter Sicherheitsseite
- vollständige Qualitätsprüfung vor jeder Veröffentlichung
- 60 Minuten Belegungspuffer vor und nach jedem Spiel
