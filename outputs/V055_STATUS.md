# V0.5.5 – FotMob All-Leagues Daily Collector

Stand: 31.08.2026, Europe/Berlin
Basis: V0.5.4 / V0.5.4.1

## Gesamtstatus

```text
FOTMOB_V055_STATUS = PASS
ALL_LEAGUES_DAILY_FEED = ENABLED
ALL_DAILY_FIXTURES_INDEXED = PASS
FIRST_HALF_REQUIRED = PASS
NO_HALFTIME_DETAILS_ARCHIVED = PASS
COUNTRY_LEAGUE_SEASON_FILTERS = PASS
AUTOMATED_FOTMOB_WORKER = OFF_BY_DESIGN
FULL_REAL_DETAIL_BACKFILL = PASS
V0551_FIVE_DAY_CANARY = PASS
V0551_DETAIL_WORKERS = 10
```

## Umgesetzter Umfang

Der Standardweg der Datumsauswahl ist jetzt nicht mehr auf Bundesliga oder
eine andere einzelne Liga begrenzt. Für jeden Tag im inklusiven Von-/Bis-
Bereich werden verwendet:

- FotMob `allLeagues` für lokalisierte Länder- und Ligabezeichnungen.
- FotMob `matches` mit `date`, `Europe/Berlin`, `ccode3=DEU` und
  `includeNextDayLateNight=true`.
- FotMob `matchDetails` für jedes eindeutige Spiel aus allen Feed-Gruppen.

Die Extraktion iteriert jede Liga-Gruppe und jede Matchliste des Tagesfeeds.
Einträge aus dem Folgetag-Abschnitt bleiben erhalten und werden mit
`is_next_day=1` markiert. Eine einzelne doppelte Provider-ID erzeugt keinen
zweiten Datenbankeintrag, wird aber nicht als verlorenes Spiel gewertet.

## FirstHalf-Regel

Ein Matchdetail wird nur dann in das kanonische Match-Core-, Perioden-,
Schuss- und Event-Parquet geschrieben, wenn FotMob verwertbare
`content.stats.Periods.FirstHalf`-Metriken liefert. Halbzeitstände allein
reichen nicht aus.

Fehlen diese Metriken, bleibt der Tagesindex erhalten und der Matchindex wird
auf `SKIPPED_NO_HALFTIME` mit `data_quality=NO_HALFTIME` gesetzt. Der
`fotmob_daily_load_runs`-Datensatz zählt diese Fälle in
`skipped_no_halftime_count`. So ist nachvollziehbar, welches Spiel im
Tagesfeed stand, ohne ein unvollständiges HZ-Detailarchiv vorzutäuschen.

## Gespeicherte und angezeigte Daten

SQLite speichert je Beobachtungstag und Match-ID unter anderem Datum,
Länder-Code/-name, Liga-ID/-name, Saison-ID/-label, UTC-Anstoß, Teams,
Matchstatus, Folgetag-Markierung und Detailstatus. Die Saison im Tagesfeed
besitzt keine Provider-Season-ID und wird deshalb transparent als
konventionelles Juli–Juni-Label (`2025/26`, `2026/27`, …) abgeleitet.

Die kanonischen Detaildaten liegen unter dem konfigurierten FotMob-Archiv:

```text
match_core/
period_stats/
shots/
events/
```

Im Bereich **Data / Debug → FotMob** kann nach Land, Liga und Saison gefiltert
werden. Die Tabelle ist nach ihren Spalten sortierbar. Für jedes angezeigte
Spiel stehen Halbzeitstand und die verfügbaren FirstHalf-Metriken bereit;
zusätzliche Provider-Metriken werden ebenfalls aus `ht_extra_stats_json`
angezeigt.

## Verifizierung

Realer FotMob-Indexlauf für den 31.08.2026:

```text
Status: PASS
Feed-Gruppen: 90
Tagesfeed-Einträge: 246
Wettbewerbe: 81
Länder: 59
Folgetag-Einträge: 21
Katalog: 95 Länder, 558 Ligen
```

Zusätzlich wurde der öffentliche Matchdetail-Endpunkt für Match-ID `5071320`
mit HTTP 200 gelesen und mit vorhandenen FirstHalf-Metriken geparst. Ein
echter Archiv-Smoke-Test schrieb für Match-ID `4799756` ein Match-Core-,
Perioden-, Schuss- und Event-Archiv.

Der vollständige Fünf-Tage-Detailabruf wurde anschließend als V0.5.5.1-
Canary mit zehn Workern und dem globalen konservativen Limit von 0,5 req/s
ausgeführt. Der vollständige Nachweis mit Tages-, Coverage-, Shot- und
Event-Zählungen steht in `outputs/V0551_FIVE_DAY_CANARY_REPORT.md`.

Ergebnis des realen Laufs: 1.755 Tagesindexzeilen, 1.618 eindeutige Spiele,
610 Canonical-Match-Core-Dateien, 1.008 `SKIPPED_NO_HALFTIME`, keine
`FAILED`-Details und keine offenen Queue-Status. Die 2 Queue-Skips im
CLI-Detailresult waren bereits frische Detailzeilen; die vorhandenen
Canonical-Dateien wurden weiterverwendet.

## Tests

```text
69 passed, 1 skipped
Python compileall: PASS
Bash-Syntax deploy/activate_fotmob.sh: PASS
Bash-Syntax deploy/install_proxmox.sh: PASS
```

Der Legacy-Liga-/Season-CLI-Pfad bleibt erhalten. Ohne `--league` lädt der
`dates`-Befehl jetzt den vollständigen Tagesfeed:

```powershell
python scripts/fotmob_history.py dates --from-date 2026-08-31 --to-date 2026-08-31 --root .
```
