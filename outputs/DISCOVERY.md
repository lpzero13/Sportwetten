# Tipico Live Data – Discovery & Verifizierung

Stand: 2026-08-29  
Scope: öffentlicher Fußball-Livebereich auf https://sports.tipico.de/de/live/fussball  
Milestone: Datenzugriff verifizieren; keine UI und kein produktiver Collector in diesem Milestone.

## Ergebnis

Der öffentliche, strukturierte Datenzugriff funktioniert reproduzierbar. Für Live-Fußball sind sowohl die Eventliste als auch die vollständigen Event- und Wettmarktdaten per JSON erreichbar.

TIPICO_ACCESS_STATUS = PASS

Einschränkung: Die Anwendung enthält einen STOMP/WebSocket-Push-Kanal, aber die anonyme Feed-Antwort meldete bei der Untersuchung pushEligible: false. Push ist deshalb als vorhandener, optionaler Mechanismus dokumentiert, jedoch noch nicht als aktive anonyme Live-Verbindung bewiesen. Polling ist der verifizierte Fallback.

## Untersuchter Browserpfad

1. Öffnen von /de/live/fussball.
2. Der geladene Dokument-/DOM-Inhalt enthielt bereits strukturierte Live-Event-Zeilen, Event-IDs, Teamnamen, Spielstand/Minute, Marktbezeichnungen und Quotenbuttons. Beispiel: Event 721664310.
3. Die Haupt-JavaScript-Anwendung war Build 6.9.20-4359328164c.
4. Die Bundle-Analyse und unabhängige HTTP-Replays zeigen für den Livebereich den Program Gateway (PPGW)-REST-Pfad.
5. Das Öffnen eines Events ändert die Route auf beispielsweise /de/live/fussball/event/721664310?t=match&eventPanelMode=2; die vollständigen Daten kommen aus dem Event-Detail-Endpunkt.

Die Browserseite war ohne Account-Login erreichbar. Für die GET-Replays wurde kein Cookie- oder Auth-Header gesetzt; Browser-Cookies wurden nicht ausgelesen oder gespeichert.

## Verifizierte Endpunkte

### Live-Eventübersicht

~~~text
GET https://sports.tipico.de/v1/tpapi/programgateway/program/events/live?selectedGroupIds=1101&regionTreeSport=1101&isLoggedIn=0&licenseRegion=DE&language=de&maxMarkets=1
~~~

Antwort: HTTP 200, Content-Type: application/json.

Die Parameter entsprechen dem aktuell geladenen deutschen Fußballpfad:

- selectedGroupIds=1101: Fußball
- regionTreeSport=1101: Fußball im Regionsbaum
- isLoggedIn=0: anonyme Sicht
- licenseRegion=DE
- language=de
- maxMarkets=1: kompakter Vorschauumfang der Übersichtsseite

Der Feed enthielt unter anderem:

~~~text
availableMarkets
sportCompetitionMap
competitionEventMap
events
eventsBySport
eventsByOdds
sportCompetitionMarketCaptionsMap
counts
matchOddGroups
matchOddGroupReplacement
outrightOddGroups
playerOddGroups
sportRegions
sports
scores
pushEligible
live
~~~

In untersuchten Snapshots wurden 31, 34 bzw. 35 Fußball-Events geliefert. Die Differenz ist eine erwartete Momentaufnahme-Dynamik des Liveprogramms, kein Parsingfehler.

availableMarkets.soccer enthielt im Snapshot:

~~~text
standard
standard-rest
points-more-less-rest
next-point
handicap
score-both
section-points-more-less
double-chance
head-to-head
~~~

### Vollständige Eventdetails und Wettmärkte

~~~text
GET https://sports.tipico.de/v1/tpapi/programgateway/program/events/{eventId}?language=de&isLoggedIn=0&licenseRegion=DE
~~~

Der Endpunkt antwortete sowohl mit den genannten Parametern als auch ohne Querystring mit HTTP 200 und Content-Type: application/json.

Verifizierte Top-Level-Struktur:

~~~text
event
categories
categoryOddGroupMap
categoryOddGroupMapSectioned
oddGroups
oddGroupResultsMap
results
oddsDividedOnColumns
categoryOddsDividedOnColumns
~~~

Der Detailendpunkt ist der relevante Endpunkt für „alle verfügbaren Märkte“. Bei Event 721525010 wurden beispielhaft 37 Marktdefinitionen, 34 Marktgruppen mit Ergebnissen und 129 Outcomes geliefert; die Anzahl ist live- und eventabhängig.

## Datenmodell für den späteren Collector

### Event, Wettbewerb und Zuordnung

- events ist im Übersichtsfeed nach Event-ID indiziert.
- event.id ist als String zu behandeln.
- event.competitionId identifiziert Wettbewerb/Liga; der Feed stellt zusätzlich sportCompetitionMap und competitionEventMap bereit.
- event.type war live, event.status/eventState waren bei laufenden Spielen running.
- Relevante Eventfelder: eventName, team1, team2, team1Id, team2Id, eventStartTime, competitionId, betMarketsCount, sportRadarMatchId, betGeniusId, redCards, extraTime, penalties, breakBefore.

### Markt und Outcome

Die stabile ID-Kette ist:

~~~text
categoryOddGroupMapSectioned
  -> oddGroupIds[]
  -> oddGroups[marketId]
  -> oddGroupResultsMap[marketId]
  -> results[outcomeId]
~~~

Eine Marktdefinition in oddGroups[marketId] enthält unter anderem:

~~~text
id
caption
type
fixedParam
standard
shortCaption
~~~

Beispiele für type/fixedParam:

~~~text
standard                         "Tipp"
next-point                       "1"
points-more-less-rest            "1:1.5"
handicap                         "1:0"
team-points-more-less            "1:1.5"
~~~

Ein Outcome in results[outcomeId] enthält typischerweise:

~~~text
id
caption
choiceParam
quote
quoteFloatValue
status            optional
~~~

Für Speicherung und Berechnung sollte quoteFloatValue als numerischer Wert verwendet werden. quote ist die Anzeigeform als String; sie kann beispielsweise "30" statt "30.00" enthalten. Markt- und Outcome-IDs sollten in Python/SQLite als 64-bit Integer oder String behandelt werden, nicht als Float.

categoryOddGroupMapSectioned ist eine Anzeige-/Sortierzuordnung und kann denselben Markt in mehreren Kontexten enthalten. Beim Normalisieren nach Markt-ID deduplizieren; fixedParam dabei unverändert behalten, weil es Linie, Team oder Spielperiode beschreibt.

## Spielstand, Minute und Phase

Im Übersichtsfeed liegt der Spielstand separat:

~~~text
scores[eventId].currentScore
scores[eventId].htScore
~~~

Im Detailfeed liegt er zusätzlich im Event:

~~~text
event.eventScores.currentScore
event.eventScores.htScore
~~~

Für die Live-Uhr stehen im Detailfeed unter anderem zur Verfügung:

~~~text
event.date
event.clockData.sectionSecondsPlayed
event.clockData.clockTimestamp
event.clockData.currentSectionDuration
event.clockData.passedSectionsTime
event.clockData.sectionTime
event.clockData.sectionNumber
event.extraTime
event.penalties
event.breakBefore
~~~

event.date kann Werte wie 79', 80' oder 120'+1 tragen und darf daher nicht auf ein starres Minutenformat reduziert werden. Im Browser wurden außerdem die Präsentationslabels HZ, VERL und ET beobachtet. Für die Normalisierung sollten die strukturierten Felder und eventPoints maßgeblich sein; eventPoints enthält strukturierte In-Game-Aktionen wie Tor/Score, Karten und Ecken mit Minute und Periodenflags.

## Quotenstatus und Suspension

Suspension ist strukturiert erkennbar. Beispiel aus einem Detailfeed:

~~~json
{
  "id": 233210274710,
  "caption": "-",
  "quoteFloatValue": 1.0,
  "status": "paused",
  "choiceParam": "-"
}
~~~

Bei diesem pausierten Outcome fehlte quote. Der Wert quoteFloatValue: 1.0 darf deshalb nicht als gültige angebotene Quote interpretiert werden. Für die UI und Berechnung gilt: status == "paused" oder fehlende Quote als nicht wählbar behandeln; den Rohdatensatz trotzdem speichern.

## Updateverhalten und Push

### Verifiziertes Polling

Ein kurzer Lauf mit drei Detailabfragen im Abstand von fünf Sekunden für Event 721349110 ergab:

- 3/3 HTTP 200
- Antwortzeit ca. 111–125 ms in dieser Untersuchung
- Spielstand 0:1 blieb konsistent
- Spielminute wechselte von 79' auf 80'
- betMarketsCount blieb bei 31
- die Standardmarktquoten waren strukturell vollständig und in diesem kurzen Fenster unverändert

Die Antworten enthielten:

~~~text
Cache-Control: must-revalidate, max-age=2, private
~~~

Das Bundle nennt zusätzlich liveEventPanelPollingInterval: 4000 ms und Fallback-Konfigurationen mit 4000 ms für Live-Polling. Für einen ersten Collector ist ein moderates Polling des Livefeeds sowie ein Detailabruf bei Auswahl/Änderung eines Events der belastbare Implementierungspfad.

### WebSocket/STOMP im Bundle

Der Build enthält einen separaten Program-Push-Kanal:

~~~text
wss://push.tipico.com/v1/tpapi/programgateway/events
~~~

Das Bundle verwendet STOMP über WebSocket und kennt unter anderem:

~~~text
/user/onSubscribe/v2
/user/topic/serverTime
/topic/program/live
/topic/eventsdeltas/v2/{eventId}
~~~

Der Seed für ein Event enthält event, matchOddGroups, outrightOddGroups und matchOddGroupReplacement; Event-Delta-Nachrichten werden im Client zusammengeführt. Für die STOMP-Verbindung setzt der Client Metadaten flcChannel, flcLicenseId und language.

Die Laufzeitkonfiguration enthielt event.pushData, program.pushData, eventPPGW und pushMechanism als aktiv. Die tatsächlich gelesene anonyme REST-Feed-Antwort meldete jedoch pushEligible: false. Daher ist Push aktuell als optionaler Pfad mit offenem Verifizierungspunkt zu behandeln; kein PoC darf seinen Erfolg allein aus den Bundle-Flags ableiten.

Der Bundle-Code enthält außerdem den generischen Endpoint /v1/tpapi/ppfes/graphql. Für den verifizierten Live-Event-/Marktpfad war GraphQL nicht erforderlich; der aktive Datenfluss lief über PPGW-REST.

## Header, Cookies und Session

Für die öffentlichen GET-Replays waren keine Tipico-spezifischen Authentifizierungs-, CSRF- oder Cookie-Werte notwendig. Ein Request ohne eigene Custom-Header lieferte ebenfalls JSON mit HTTP 200. Die Query-Parameter isLoggedIn=0, licenseRegion=DE und language=de sollten trotzdem mitgeführt werden, weil sie den vom Browser verwendeten Kontext explizit machen.

Für REST ist damit im PoC zunächst ausreichend:

~~~text
GET
Accept: application/json        optional, aber sinnvoll
User-Agent: normaler HTTP-Client optional
keine Authentifizierung
keine manuell übernommenen Browser-Cookies
~~~

Für den optionalen Push-Pfad sind die oben genannten STOMP-Metadaten relevant. Ihre anonyme Laufzeitbeschaffung und eine tatsächliche Delta-Nachricht wurden in diesem Milestone nicht als erfolgreich bestätigt.

## Risiken und offene Punkte

- Liveantworten sind zeitabhängig; Events können während der Untersuchung starten, pausieren oder enden.
- Der Übersichtsfeed mit maxMarkets=1 ist kein Ersatz für den Detailabruf aller Märkte.
- quote kann bei status: "paused" fehlen; keine Fallbackquote aus quoteFloatValue erzeugen.
- Marktzuordnungen können durch Kategorien/Anzeigegruppen doppelt erscheinen; nach IDs deduplizieren.
- Die Feature Flags können den Feed künftig auf den Legacy-Pfad /program/liveEvents umleiten. Der aktuell verifizierte Pfad ist /v1/tpapi/programgateway/program/events/live.
- Eine kontrollierte 10-Minuten-Stabilitätsmessung mit Quote-Änderungen, Reconnects und echter Push-Delta-Nachricht steht noch aus.
- Das zugängliche Browser-Automationsinterface liefert keine vollständige DevTools-Network-Historie. Die Requestpfade wurden deshalb aus dem geladenen Bundle abgeleitet und anschließend mit unabhängigen öffentlichen HTTP-GETs verifiziert.
- Consent-/Analytics- und ein nicht kritischer Service-Worker-Registrierungslog blockierten den strukturierten Datenzugriff nicht.

## Verifikationslog

| Prüfung | Ergebnis |
|---|---|
| Öffnen des öffentlichen Live-Fußballpfads | Erfolgreich; Live-Events und Märkte sichtbar |
| Eventliste im Browser-DOM | Event-IDs, Teams, Minute, Spielstand, Marktanzahl und Quotenbuttons vorhanden |
| PPGW-Livefeed | HTTP 200, JSON, 31–35 Fußball-Events je Snapshot |
| Eventdetail 721525010 | HTTP 200 mit vollständiger Markt-/Outcome-Struktur |
| Eventdetail ohne Querystring | HTTP 200, JSON |
| GET ohne eigene Custom-Header | HTTP 200, JSON |
| Standardmarkt mit 1/X/2 | Struktur und numerische Quoten verifiziert |
| Pausiertes Outcome | status: "paused" und fehlendes quote verifiziert |
| Mehrfachabfrage über ca. 10 Sekunden | 3/3 HTTP 200; Minute und Payload-Struktur konsistent |
| Program-Push | Bundle/Topics gefunden; anonyme Aktivierung wegen pushEligible: false noch offen |

TIPICO_ACCESS_STATUS = PASS
