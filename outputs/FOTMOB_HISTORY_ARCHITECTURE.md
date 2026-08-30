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
transaktionale SQLite-Claim-Operation worker-aware und resumierbar.

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

Der Parser übernimmt nur explizit gelieferte HT-Scores und HT-Statistiken.
Ein fehlender HT-Wert wird nicht aus dem Endstand oder aus Vollzeitstatistiken
abgeleitet. FT- und HT-Statistiken erhalten getrennte Spaltenpräfixe;
unbekannte Paare bleiben in JSON erhalten. Timeline-Ereignisse werden
normalisiert und zusätzlich raw-nah gespeichert.

Das Ziel `second_half_goals` ist nur bei vollständigen, nicht widersprüchlichen
HT-/FT-Scores definiert. Die Klassen sind `0`, `1` und `2_PLUS`.

## Archive contract

```text
data/archive/fotmob/historical/
└── league_id=54/
    ├── season=2025-26/*.parquet
    └── season=2024-25/*.parquet
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

Die Pipeline akzeptiert lokale Fixtures für Tests. Externe Discovery und
Detailrequests verlangen gleichzeitig:

```text
FOTMOB_ENABLED=true
FOTMOB_HISTORY_ENABLED=true
FOTMOB_PROVIDER_DECISION=PRODUCTION_READY
FOTMOB_AUTOMATED_USAGE=ACCEPTABLE_FOR_PROJECT
```

Bei den aktuellen V0.5.1-Werten `LIMITED_USE`/`UNCLEAR` wird kein externer
Historical-Request ausgeführt. Das schützt zugleich Tipico-/Paper-Betrieb
vor einer FotMob-Abhängigkeit.
