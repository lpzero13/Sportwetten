# FotMob Discovery – V0.5

Stand: 30.08.2026, Europe/Berlin

## Ergebnis

`FOTMOB_ACCESS_STATUS = PARTIAL`

**Status: browser-verifiziert, Automationsvertrag noch offen.**

Die V0.5.1-Abschlussentscheidung ist in
[FOTMOB_FINAL_VALIDATION.md](FOTMOB_FINAL_VALIDATION.md) dokumentiert:
`FOTMOB_PROVIDER_DECISION = LIMITED_USE` und
`AUTOMATED_USAGE = UNCLEAR`.

Die öffentliche FotMob-Webseite wurde mit dem Browser geprüft. Sichtbar und
ohne Login gefunden wurden:

- Liga, Land/Region und Saison. Die Bundesliga-Seite zeigt `Bundesliga` sowie
  `Deutschland` und bietet historische Saisons bis mindestens 2010/2011 an.
- Match-Metadaten, Heim-/Auswärtsteam, Anstoß, Wettbewerb, Spieltag,
  Endstand, Halbzeitstand und eine Ereignis-/Tor-Timeline.
- Vollzeit-Statistiken wie xG, Schüsse, Schüsse aufs Tor, Großchancen, Ecken,
  Ballbesitz und Karten.
- Ein Periodenfilter `1.` auf einer beendeten Bundesliga-Partie. Damit waren
  historische Halbzeitwerte für Ballbesitz, xG, Schüsse, Schüsse aufs Tor,
  Großchancen und Ecken sichtbar. Diese Werte werden nicht aus den
  Vollzeitwerten abgeleitet.

Geprüfte öffentliche Seiten:

- [FotMob Startseite](https://www.fotmob.com/)
- [FotMob Bundesliga](https://www.fotmob.com/de/leagues/54/overview/bundesliga)
- [Bayern München – VfB Stuttgart, Beispielmatch](https://www.fotmob.com/de/matches/bayern-munchen-vs-vfb-stuttgart/3c6nc5#5881143)

## Mindestantworten der Discovery

| Frage | Ergebnis | Begründung |
|---|---|---|
| Upcoming Matches verfügbar? | `PARTIAL` | Fixtures und Anstoß auf der Liga-Seite sichtbar; strukturierter Serienabruf offen |
| Live Matches verfügbar? | `PARTIAL` | öffentliche Live-Navigation vorhanden; echter strukturierter Live-Sample offen |
| Historische Matches verfügbar? | `PASS` für geprüfte Liga | beendete Bundesliga-Matchseite und Saisonwahl sichtbar |
| Historischer HT Score verfügbar? | `PASS` für Beispielmatch | Timeline und HZ-Stand sichtbar |
| Historische HT Stats verfügbar? | `PASS` für Beispielmatch | `1.`-Periodenfilter zeigt Stats; Liga-/Saisonabdeckung offen |
| Live xG / Shots / Shots OT verfügbar? | `OPEN` | im historischen Beispiel sichtbar, live noch nicht belastbar verifiziert |
| Live Big Chances / Corners verfügbar? | `OPEN` | im historischen Beispiel sichtbar, live noch nicht belastbar verifiziert |
| Live Cards / Possession verfügbar? | `OPEN` | im historischen Beispiel sichtbar, live noch nicht belastbar verifiziert |
| Login erforderlich? | `NO` im Browser-Test | öffentliche Seiten ohne Login geöffnet |
| Cookies/Tokens erforderlich? | `OPEN` für strukturierte Automation | keine Zugangsdaten verwendet; Endpoint-Vertrag nicht zugesichert |
| Public structured access? | `PARTIAL` | Parser/Client vorhanden, aber kein stabiler Vertrag formal bestätigt |

Die Beispielpartie war sichtbar als Bayern München 5:1 VfB Stuttgart. Die
Seite zeigte unter dem Statistikfilter für die erste Halbzeit unter anderem
56:44 Ballbesitz, 1,34:0,40 xG, 9:4 Schüsse, 5:0 Schüsse aufs Tor, 3:1
Großchancen und 5:2 Ecken. Das beweist UI-Verfügbarkeit für dieses Beispiel,
aber noch keine stabile öffentliche JSON-Schnittstelle.

## Was noch nicht als PASS gilt

In dieser Discovery wurde kein belastbarer, versionsstabiler API-Vertrag für
automatisierte Massenabfragen freigegeben. Live- und Upcoming-Sampling über
strukturierte Antworten, verschiedene Wettbewerbsgrößen, Frauen-/Jugendspiele,
Rate-Limit-Verhalten und eine längere Laufzeit sind daher **PARTIAL** bzw.
**OPEN**. Eine sichtbare Webseite ist nicht automatisch eine Zusicherung, dass
der dahinterliegende Endpoint dauerhaft automatisiert genutzt werden darf.

FotMob weist auf der öffentlichen Seite außerdem auf Einschränkungen gegen
automatische, systematische oder regelmäßige Nutzung hin. Deshalb gilt im
Projekt:

- `FOTMOB_ENABLED=false` bleibt der Default.
- Kein Login, keine Cookies, keine Tokens und kein Bulk-Crawler.
- Der Client hat Timeout, maximal drei Retries mit 1/3/10 Sekunden,
  konfigurierbares Mindestintervall und einen Kill-Switch.
- Der optionale Worker liest nur bereits bestätigte Match-Links.
- Bei Nichtverfügbarkeit bleibt Tipico einschließlich Paper Trading nutzbar.

## Parser- und Storage-Entscheidungen

- Fehlende Werte bleiben `NULL`/`None`; fehlende Halbzeitstatistik wird nicht
  als Null und nicht aus der zweiten Halbzeit oder dem Endstand geschätzt.
- Ballbesitz wird als Prozentwert 0 bis 100 gespeichert, nicht als Bruch.
- Numerische Strings mit Punkt, Komma oder Prozentzeichen werden akzeptiert.
- Nachspielzeit wird in `minute` und `added_time` getrennt gespeichert.
- Tore, Karten und andere Ereignisse werden mit Minute, Teamseite und Raw-
  Restfeldern erhalten.
- Unbekannte Statistikfelder bleiben in `extra_stats_json` und im Snapshot-
  Payload erhalten.
- Current State wird pro internem Match ersetzt. Historie entsteht nur über
  die sieben Slots `PRE_KICKOFF`, `HALFTIME`, `HT_STABLE`, `MINUTE_60`,
  `MINUTE_70`, `MINUTE_80` und `FINAL`.

## Reproduzierbare Probe

Ohne Netzwerk zeigt der Befehl nur Konfiguration und Policy:

```powershell
python scripts/discover_fotmob.py --root .
```

Ein einzelner aktiver Probeabruf benötigt eine explizite Freigabe im Env:

```powershell
$env:FOTMOB_ENABLED="true"
python scripts/discover_fotmob.py --root . --match-id 5881143
```

Dieser Probeabruf ist absichtlich auf ein Match begrenzt. Die Nutzung muss
mit den jeweils geltenden Bedingungen der Quelle vereinbar sein. Ein
periodischer Worker ist in V0.5.1 wegen der Provider-Entscheidung deaktiviert;
Details stehen in [FOTMOB_PROVIDER_DECISION.md](FOTMOB_PROVIDER_DECISION.md).
