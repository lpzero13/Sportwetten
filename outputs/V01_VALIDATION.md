# Tipico Live Observer V0.1 – Final Validation

Testdatum: 29.08.2026  
Testumgebung: Windows, Python 3.12.10, Streamlit 1.62.0  
Scope: öffentlicher Tipico-Live-Fußballfeed, read-only

## Ergebnis

Der öffentliche REST-Zugriff, die Fußballfilterung, die Detailauflösung, die
vollständige Marktanzeige und die Historisierung wurden mit einem echten
60-Minuten-Lauf verifiziert:

TIPICO_V01_STATUS = PASS

Der ursprüngliche Smoke-Test hatte auch Nicht-Fußball-Events aus demselben
Tipico-Payload gezählt. Das wurde vor der Finalvalidierung korrigiert: Der
Parser verwendet jetzt die von Tipico gelieferte `eventsBySport.soccer`-ID-Liste.
Der anschließende Browservergleich zeigte dieselbe Fußball-Eventmenge wie die
Originalseite.

## Automatischer Live-Smoke-Test nach der Korrektur

Ausgeführt mit:

~~~powershell
python scripts/validate_v01.py --root work/validation-runtime-v02-check
~~~

Reale Ergebnisse:

| Kennzahl | Ergebnis |
|---|---:|
| getestete Live-Fußball-Events | 48 |
| getestete Wettbewerbe | 23 |
| getestete Eventdetails | 1 |
| ausgewähltes Event | `720985110` |
| höchste Übersicht-Marktanzahl | 69 |
| Märkte im ausgewählten Detail | 37 |
| Outcomes im ausgewählten Detail | 130 |
| API Requests | 2 |
| HTTP/API-Fehler | 0 |
| API-Fehlerquote | 0,0 % |
| Parsingfehler | 0 |
| Raw Storage | ON |

Im Detail wurden auch unbekannte Marktarten übernommen, nicht verworfen,
unter anderem `correct-score-live`, `team-points-more-less`,
`team-number-of-points`, `team-scores`, `odd-even` und
`double-chance-total`.

## Browservergleich mit einem großen Halbzeit-Event

Originale Tipico-Seite und lokale Streamlit-Anwendung wurden parallel mit
demselben Event geöffnet:

| Feld | Wert |
|---|---|
| event_id | `720985110` |
| Wettbewerb | K-League 2, Südkorea / K-League 2 |
| Teams | FC Hwaseong – Cheongju FC |
| Spielstand | 0:0 |
| Spielphase | Tipico HZ, lokal `HALF_TIME` bzw. danach `LIVE` |
| lokale Detailgröße | 37 Märkte, 130 Outcomes |
| Tipico-Seite | 37-marktiges Eventangebot sichtbar |
| Ergebnis des synchronisierten Stichvergleichs | 36/36 = 100,0 % |

Die folgenden 36 verfügbaren Quoten wurden am 29.08.2026 gegen den sichtbaren
Tipico-Stand und die lokale Detailansicht geprüft. Gemeinsamer Prüfzeitpunkt:
`2026-08-29T11:33:41Z` bis `2026-08-29T11:34:19Z`.

| Markt | Outcome | tipico_web_odds | local_odds | match |
|---|---|---:|---:|---|
| Tipp | 1 | 2,40 | 2,40 | yes |
| Tipp | X | 2,30 | 2,30 | yes |
| Tipp | 2 | 4,00 | 4,00 | yes |
| Nächstes Tor | 1 | 1,90 | 1,90 | yes |
| Nächstes Tor | X | 3,60 | 3,60 | yes |
| Nächstes Tor | 2 | 3,10 | 3,10 | yes |
| Über/Unter Restzeit 0,5 | + | 1,22 | 1,22 | yes |
| Über/Unter Restzeit 0,5 | - | 3,60 | 3,60 | yes |
| Über/Unter Restzeit 1,5 | + | 2,20 | 2,20 | yes |
| Über/Unter Restzeit 1,5 | - | 1,55 | 1,55 | yes |
| Über/Unter Restzeit 2,5 | + | 4,30 | 4,30 | yes |
| Über/Unter Restzeit 2,5 | - | 1,16 | 1,16 | yes |
| Tipp Restzeit | 1 | 2,40 | 2,40 | yes |
| Tipp Restzeit | X | 2,30 | 2,30 | yes |
| Tipp Restzeit | 2 | 4,00 | 4,00 | yes |
| Handicap 0:1 | 1 | 7,50 | 7,50 | yes |
| Handicap 0:1 | X | 3,20 | 3,20 | yes |
| Handicap 0:1 | 2 | 1,45 | 1,45 | yes |
| Beide Teams treffen | J | 3,10 | 3,10 | yes |
| Beide Teams treffen | N | 1,27 | 1,27 | yes |
| Doppelte Chance | 1X | 1,16 | 1,16 | yes |
| Doppelte Chance | 12 | 1,45 | 1,45 | yes |
| Doppelte Chance | X2 | 1,45 | 1,45 | yes |
| Head-To-Head | 1 | 1,55 | 1,55 | yes |
| Head-To-Head | 2 | 2,20 | 2,20 | yes |
| FC Hwaseong Über/Unter 0,5 | + | 1,60 | 1,60 | yes |
| FC Hwaseong Über/Unter 0,5 | - | 2,10 | 2,10 | yes |
| Cheongju FC Über/Unter 0,5 | + | 2,10 | 2,10 | yes |
| Cheongju FC Über/Unter 0,5 | - | 1,60 | 1,60 | yes |
| Tor FC Hwaseong? | J | 1,60 | 1,60 | yes |
| Tor FC Hwaseong? | N | 2,10 | 2,10 | yes |
| Anzahl Tore FC Hwaseong | 0 | 1,95 | 1,95 | yes |
| Anzahl Tore FC Hwaseong | 1-2 | 1,75 | 1,75 | yes |
| Anzahl Tore FC Hwaseong | 3+ | 16,00 | 16,00 | yes |
| Tor Cheongju FC? | J | 1,95 | 1,95 | yes |
| Tor Cheongju FC? | N | 1,70 | 1,70 | yes |

Alle geprüften Outcomes waren in diesem Sample `OPEN`. Die lokale Seite zeigte
die Quoten in den Markt-Dataframes; der Vergleich wurde zusätzlich gegen den
unmittelbar gleichen normalisierten Detailpayload nachvollzogen. Zeitversatz
zwischen zwei Browser-/REST-Abfragen kann bei Livequoten dennoch einzelne
Quoteänderungen erklären.

## Halbzeitbeobachtung

Das gewählte Event befand sich beim Vergleich zunächst bei 46–49 Minuten. Die
lokale Detailansicht wurde mit aktivierter Detail-Aktualisierung (5 Sekunden)
offen gehalten. In der gespeicherten Timeline stehen für dieses Event
`HALF_TIME` mit 0:0, danach die zweite Halbzeit und später `FINISHED` mit 2:1.
Der Fußballfeed erfasste zusätzlich bei Event `720979610` einen vollständigen
`45'+3 → HZ → 46'`-Übergang.

Der maschinenlesbare Halbzeit-Inventarreport für Event `720985110` liegt unter
`data/halftime_reports/2026-08-29/720985110_latest.json` und enthält 37 Märkte,
130 Outcomes sowie pro Markt `market_id`, `type`, `caption`, `fixedParam`,
Status, Outcomes und Quoten. Das Raw-Payload bleibt zusätzlich unter
`data/raw/2026-08-29/events/720985110/halftime/` archiviert.

## Langzeittest

Gestartet mit einem dauerhaft laufenden Client und ohne Neustart:

~~~powershell
python scripts/validate_v01_long.py --root . --event-id 720985110 --duration-minutes 60 --streamlit-url http://localhost:8505
~~~

Der abgeschlossene Lauf ist in `data/validation_v01_long.json` dokumentiert.
Er lief ohne manuellen Neustart von `2026-08-29T11:41:31Z` bis
`2026-08-29T12:41:31Z`.

| Kennzahl | Livefeed | Eventdetail |
|---|---:|---:|
| Requests | 360 | 720 |
| HTTP-Fehler | 0 | 0 |
| Fehlerquote | 0,0 % | 0,0 % |
| Median Antwortzeit | 177 ms | 117 ms |
| P95 Antwortzeit | 256,05 ms | 207 ms |
| Maximum Antwortzeit | 477 ms | 3.402 ms |
| Durchschnitt Antwortzeit | 182,55 ms | 131,85 ms |
| Durchschnitt Payload | 90.556,64 Bytes | 14.735,73 Bytes |
| maximale Payload | 98.236 Bytes | 28.211 Bytes |

Die 120 von 120 Streamlit-Healthchecks waren erfolgreich. Es gab 0 Service-,
Parsing- oder Datenbankfehler.

Im Test wurden echte Score-/Marktänderungen erfasst. Beispiel für ein
Outcome: `OPEN @ 8,00` → `stopped / nicht verfügbar` um
`11:52:01Z` → `OPEN @ 2,60` um `11:52:16Z`. Identische aufeinanderfolgende
History-Zustände wurden nicht dupliziert; die abschließende SQL-Prüfung ergab
0 identische Folgezeilen.

Gemessen werden getrennt für Livefeed und Eventdetail:

- Requestanzahl und HTTP-Fehlerquote
- Median, P95, Maximum und Durchschnitt der Antwortzeit
- durchschnittliche und maximale Payloadgröße
- Parsing-/Servicefehler
- Streamlit-Healthchecks
- SQLite-Zeilen nach Testende.

## Parser-, Datenbank- und UI-Tests

~~~text
7 passed in 0.35s
~~~

Abgedeckt sind Event-/Competition-Zuordnung, Spielstand, Minute und
Clock-Section, die Kategorie-Markt-Outcome-ID-Kette, Marktdeduplizierung,
numerische Quoten, pausierte Outcomes, deduplizierte Odds-History,
Quote-/Statusänderungen, Raw-Payload-Speicherung und verifizierte REST-Pfade.

Der Streamlit-Healthcheck lieferte `HTTP 200 / ok`. Ein AppTest prüfte einen
initialen Lauf ohne Exceptions, die Live-Seite, den Quoten-Klick und das
Eventdetail. Für das große Event wurden alle 37 gelieferten Märkte gerendert;
die drei leeren `points-more-less-than`-Marktgruppen bleiben sichtbar und
werden nicht stillschweigend entfernt.

## Acceptance-Kriterien

| Kriterium | Status |
|---|---|
| lokale Anwendung startet | PASS |
| Fußball-Events korrekt aus dem Feed selektiert | PASS |
| dynamische Competition-Zuordnung | PASS |
| Eventdetail nur für Auswahl laden | PASS |
| alle gelieferten Märkte/Outcomes anzeigen | PASS für großes Detail-Event |
| pausierte Outcomes nicht wettbar | PASS per Fixture-/Unit-Test |
| SQLite-Historisierung | PASS per Unit-Test |
| 36 Quote-zu-Quote-Vergleiche | PASS, 100,0 % im dokumentierten Sample |
| echte Quoteänderung über längere Zeit | PASS |
| Suspension/Reopen | PASS (`stopped` → `OPEN`, 11:52:01Z → 11:52:16Z) |
| vollständiger Halbzeitübergang im Fußballfeed | PASS (`720979610`) |
| vollständiges Halbzeit-Markt-Inventar | PASS (`720985110`, 37/130) |
| mindestens 60 Minuten stabil | PASS (60,0003 Minuten, kein Neustart) |

TIPICO_V01_STATUS = PASS

V0.1 ist damit abgeschlossen. Wettstrategie, ROI, EV, Ranking, FotMob und
Einsatzverteilung sind weiterhin nicht Bestandteil dieser Validierung.
