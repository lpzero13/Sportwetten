# Altimplementierungen FotMob – V0.5.2 Portierungsbericht

Stand: 30.08.2026, Europe/Berlin  
Untersuchte Projekte:

- `C:\Programmieren\Goal Analyser`
- `C:\Programmieren\Fussball`

## Kurzfazit

Die funktionierende historische FotMob-Implementierung befindet sich in
`Fussball`, nicht in `Goal Analyser`. Die dort verwendeten Legacy-Endpunkte
und JSON-Strukturen waren die richtige Grundlage für die Portierung. Die
Endpunkte selbst haben sich inzwischen geändert: bei der erneuten Prüfung
antworteten die alten API-Pfade mit HTTP 404; die aktuelle öffentliche Seite
liefert dieselben fachlichen Bereiche als HTML mit eingebettetem
Next.js-`__NEXT_DATA__`:

```text
GET https://www.fotmob.com/leagues/{league_id}
GET https://www.fotmob.com/leagues/{league_id}?season={season_label}
GET https://www.fotmob.com/match/{match_id}
```

Die wesentlichen Formen sind `fixtures.allMatches` bzw. `matches.allMatches`,
`status.utcTime`, `status.finished`,
`content.matchFacts.events.events`, `content.stats.Periods.{All,FirstHalf,SecondHalf}`
und `content.shotmap.shots`. Diese Erkenntnisse sind in den vorhandenen
`history_discovery.py`-/`parser.py`-Pfad eingeflossen. Die Altprogramme
wurden nicht als Ganzes kopiert, weil sie eine aggressive, SQLite-zentrierte
Bulk-Architektur ohne zentrale Rate-Limit-, Retry-, Queue- und Parquet-Garantien
verwenden.

## 1. Goal Analyser

`C:\Programmieren\Goal Analyser\Fotmob Erweiterung\background.js` ist eine
Chrome-MV3-Erweiterung für einen einzelnen, bereits geöffneten FotMob-Spieltab.
Sie:

1. prüft, ob die aktive Seite `fotmob.com` ist,
2. klickt den Statistik-Reiter und danach den Reiter `1.`,
3. liest Teams, sichtbares Ergebnis und xG aus dem DOM,
4. schreibt ein kleines JSON in die Zwischenablage.

Das Projekt enthält dort keinen API-Client, keine Season-Discovery, keinen
historischen Matchindex und keinen Bulk-Downloader. Für V0.5.2 wurde daraus
nichts portiert. Die DOM-Extraktion bleibt als möglicher manueller Fallback
außerhalb des Historical-Collectors.

## 2. Fussball – Endpunkte und Discovery

Die Dateien `Daten Sammler\sniper_ultimate_collector.py`,
`Daten Sammler\Backup\backend\app\services\collector.py`,
`GoalPredictor\backend\app\services\fotmob.py` und
`Daten Sammler\live_service.py` verwenden den damaligen League-API-Endpoint
mit einer Season als sichtbarem Label, zum Beispiel `2025/26`. Diese
Legacy-URLs sind wichtig für das Verständnis des alten Bulk-Downloaders, aber
nicht mehr der aktuelle Netzwerkpfad im neuen Projekt.

Die Discovery sucht defensiv in dieser Reihenfolge:

- `payload.fixtures.allMatches`
- `payload.matches.allMatches`
- `payload.matches`, wenn es bereits eine Liste ist

Pro Match werden die Provider-ID, `status.utcTime`, Heim-/Auswärtsteam und
der Finished-Status gelesen. Die alte Saisonlogik unterscheidet
Kalenderjahr-Ligen über eine feste Liste und verwendet sonst ab Juli
`YYYY/YYYY+1`, davor `YYYY-1/YYYY`. Für FotMob-Liga 54 ist in der alten
Ligaliste ausdrücklich `GER - Bundesliga` hinterlegt; Liga 38 ist separat
`AUT - Bundesliga`.

## 3. Fussball – Matchdetails und fachliche Felder

Der Detailabruf verwendet:

```text
/api/matchDetails?matchId={match_id}
```

Die Altparser lesen:

- Endstand aus `header.teams[*].score`,
- Ereignisse aus `content.matchFacts.events.events`,
- HT-/FT-Statistiken aus `content.stats.Periods`,
- Schüsse und xG aus `content.shotmap.shots`,
- Teamzuordnung bevorzugt über `teamId`, ersatzweise über `isHome`.

`sniper_ultimate_collector.py` übernimmt `All`, `FirstHalf` und
`SecondHalf` als flache Spalten und berechnet den Halbzeitstand aus
Torereignissen bis Minute 45. `Daten Sammler\AntiGrav\API_Test.py` verwendet
zusätzlich `ccode3=DEU` und zeigt die gewünschte 60-Minuten-Auswertung mit
HT-Score, xG, Schüssen, Karten, Momentum und einem Tor-nach-60-Ziel.

## 4. Nachweis vorhandener Bulk-Daten

Read-only geprüft wurde:

```text
Daten Sammler\AntiGrav\backend\data\sniper_football.db
matches: 17.900 Zeilen
league_id=54: 545 Zeilen, 2020-02-22 bis 2026-02-01
league_id=54 mit HT-Score: 545
league_id=54 mit 60'-xG: 545
league_id=38: 206 Zeilen, 2020-06-28 bis 2025-12-14
```

Ein vorhandener FotMob-Feature-Export für Match `4829490` enthält unter
anderem Liga 54, `Hamburger SV` – `Bayern München`, HT-/60'-Score, xG,
Schüsse, HT-Ecken und das Ziel `goal_after_60`. Damit ist nachgewiesen, dass
der Alt-Bulk-Downloader real befüllte FotMob-Daten geschrieben hat.

## 5. Technische Schwächen der Altimplementierungen

- `cloudscraper` wird mit Chrome/Windows-Profil verwendet, aber ohne zentrale
  globale Drosselung und ohne belastbare Retry-/Backoff-Policy.
- Die Bulk-Worker sind mit bis zu 50 League- und 30 Detail-Threads sehr
  aggressiv und für den V0.5.2-Validierungslauf nicht geeignet.
- Fehler werden häufig als leere Liste bzw. `None` verschluckt; eine
  persistente Failed-Queue und Stale-Recovery fehlen.
- SQLite wird per `INSERT OR REPLACE` beschrieben; atomare, schema-versionierte
  Parquet-Batches und Archiv-Deduplizierung fehlen.
- Einige ältere Feature-Skripte approximieren den Endstand aus einer
  begrenzten Ereignisauswertung oder verwenden unterschiedliche Momentum-Pfade.
  Diese Logik wird nicht als Historical-Grundlage übernommen.
- `Daten Sammler\API_Test.py` ruft API-Football auf und ist kein FotMob-Client;
  sie wurde deshalb nicht portiert.

## 6. Was in V0.5.2 wiederverwendet wurde

Übernommen bzw. gegen die aktuelle Architektur abgeglichen wurden nur die
fachlich belastbaren Providerkenntnisse:

- die fachlichen Katalog-/Detailpfade und ihre aktuelle öffentliche
  Entsprechung (`/leagues/...`, `?season=...`, `/match/...`),
- die `allMatches`-Fallbacks,
- echte Provider-Season-IDs statt aus Labels konstruierter IDs,
- `status.utcTime`-/Finished-Erkennung,
- `teamId` vor `isHome` für die Zuordnung,
- getrennte `All`-/`FirstHalf`-/`SecondHalf`-Statistiken,
- Ereignis- und Shotmap-Pfade für HT/FT, xG und Zielableitung.

Die Ausführung bleibt im bestehenden V0.5.2-Design: `off` als Default,
bewusst manueller CLI-Modus, ein globaler Request-Limiter, Timeout/Retry/
Backoff, SQLite-Queue mit Claims und Failed-Status, Stale-Recovery,
nullable Normalisierung, Data-Quality-Klassen, atomare Parquet-Batches und
Provider-ID-Deduplizierung. Ein mehrstündiger Bulk-Scan wird dadurch nicht
automatisch gestartet.

## 7. Reale Portierungsprüfung in V0.5.2

Die portierte Zugriffsschicht wurde mit Liga `54` gegen die aktuelle
öffentliche Seite geprüft. Discovery ergab `Bundesliga`, Country-Code `GER`
und die echten Season-IDs `26891` (`2025/2026`) sowie `23794`
(`2024/2025`). Für beide Seasons wurden jeweils `306` Fixtures indexiert.
Je fünf deterministisch ausgewählte Finished-Matches wurden über
`/match/{match_id}` geladen; alle `10/10` wurden als `FETCHED/COMPLETE` mit
HT-/FT-Scores, HT-/FT-Statistiken, Timeline, Zielwert und `ml_eligible=1`
archiviert. Der zweite Lauf ergab `0` weitere Requests, womit Resume und
Archiv-Deduplizierung bestätigt sind.
