# FotMob Historical Architecture – V0.5.2

Stand: 30.08.2026, Europe/Berlin

## Pipeline

```text
league discovery (54)
        |
        v
fotmob_seasons  -- real season_id + label
        |
        v
fixture/results index  -->  fotmob_match_index
        |                         |
        |                         +--> deterministic sample (0/25/50/75/100 %)
        v                         |
detail queue <-------------------+
        |
        v
defensive parser: explicit HT + FT + timeline + nullable stats
        |
        +--> SQLite catalog / queue state
        +--> flat zstd-Parquet archive
        +--> optional zstd raw payload (development/sample only)
```

Discovery/Indexierung und Detailscan sind getrennte Jobs. Der Index kennt nur
Metadaten und behauptet keine Detaildaten. Der Scan wird durch eine
transaktionale SQLite-Claim-Operation worker-aware und resumierbar. Die
verifizierten aktuellen öffentlichen Pfade sind:

```text
/leagues/{league_id}
/leagues/{league_id}?season={season_label}
/match/{match_id}
```

Die Antworten sind HTML mit eingebettetem Next.js-`__NEXT_DATA__`; die
historischen IDs stammen aus `stats.seasonStatLinks[].TournamentId`.

## SQLite contract

### `fotmob_seasons`

Primärschlüssel ist `(provider, league_id, season_id)`. Gespeichert werden
Season-ID, sichtbares Label, Liga, Land sowie Discovery-/Check-Zeitpunkte.

### `fotmob_match_index`

`fotmob_match_id` ist der Provider-Schlüssel. Neben Liga, Season, Teams,
Kickoff, Runde und Matchstatus liegen dort `detail_status`,
`attempt_count`, `last_attempt_at`, `last_error`, `worker_id`,
`data_quality`, `ml_eligible`, Parser-/Schema-Version, Raw-Pfad, Payload-Hash
und das berechnete Second-Half-Ziel.

Queue-Zustände:

```text
NOT_FETCHED -> IN_PROGRESS -> FETCHED
                           \-> PARTIAL
                           \-> NOT_FETCHED (retry)
                           \-> FAILED (attempt limit)
```

`IN_PROGRESS`-Claims, die älter als 30 Minuten sind, werden vor einem neuen
Claim zurückgesetzt. `--retry-failed` erlaubt einen expliziten erneuten Lauf,
ohne die maximale Versuchszahl zu überschreiten.

## Detail contract

Der Parser übernimmt explizit gelieferte HT-Scores und HT-Statistiken. Bei der
aktuellen öffentlichen Payload wird der HT-Score, falls FotMob ihn nicht als
eigenes Feld liefert, ausschließlich aus der Goal-Timeline abgeleitet:
`newScore` nach einem Tor in Halbzeit 1 bzw. der Score vor dem ersten Tor in
Halbzeit 2. Ein beendetes 0:0 ist mathematisch eindeutig 0:0 zur Halbzeit.
Eine Ableitung aus Vollzeitstatistiken findet nicht statt. FT- und
HT-Statistiken erhalten getrennte Spaltenpräfixe; unbekannte Paare bleiben in
JSON erhalten. Timeline-Ereignisse werden normalisiert und zusätzlich raw-nah
gespeichert.

Das Ziel `second_half_goals` ist nur bei vollständigen, nicht widersprüchlichen
HT-/FT-Scores definiert. Die Klassen sind `0`, `1` und `2_PLUS`.

## Archive contract

```text
data/archive/fotmob/historical/
└── league_id=54/
    ├── season=2025-2026/*.parquet
    └── season=2024-2025/*.parquet
```

Parquet wird batchweise mit `PARQUET_COMPRESSION=zstd` und atomarem
Temporary-File-Rename geschrieben. Die Deduplizierung erfolgt in
`fotmob_historical_archive_index` nach `(provider, fotmob_match_id,
schema_version)`. Ein erneuter Lauf schreibt bereits archivierte Zeilen nicht
noch einmal.

Entwicklungs-Raw ist standardmäßig ausgeschaltet und kann ausschließlich über
`STORE_FOTMOB_HISTORICAL_RAW=true` aktiviert werden. Die Pfade liegen dann
unter `data/raw/fotmob/historical/league_id=.../season=.../`.

## Policy boundary

Die Pipeline akzeptiert lokale Fixtures für Tests. Der permanente Worker
verlangt gleichzeitig:

```text
FOTMOB_ENABLED=true
FOTMOB_HISTORY_ENABLED=true
FOTMOB_NETWORK_MODE=worker
FOTMOB_PROVIDER_DECISION=PRODUCTION_READY
FOTMOB_AUTOMATED_USAGE=ACCEPTABLE_FOR_PROJECT
```

Für einen bewusst gestarteten, begrenzten CLI-Lauf gilt dagegen ausschließlich:

```text
FOTMOB_ENABLED=true
FOTMOB_HISTORY_ENABLED=true
FOTMOB_NETWORK_MODE=manual
```

Damit kann die V0.5.2-Abnahme ohne Worker-Automation erfolgen. Standard ist
`FOTMOB_NETWORK_MODE=off`; bei den aktuellen V0.5.1-Werten
`LIMITED_USE`/`UNCLEAR` bleibt der permanente Worker gesperrt. Das schützt
Tipico-/Paper-Betrieb vor einer FotMob-Abhängigkeit.
