# V0.5.4 – FotMob Canonical Archive & Container Activation

Stand: 31.08.2026, Europe/Berlin

## Status

```text
FOTMOB_V054_STATUS = IMPLEMENTED
REAL_DATE_RANGE_SMOKE = PASS
FULL_BUNDESLIGA_BACKFILL = NOT_RUN
```

V0.5.4 stellt im Dashboard unter **Data / Debug → FotMob** eine bewusst kleine
Datumsauswahl **Von/Bis** bereit. Die Auswahl ist inklusiv und wird nach dem
FotMob-UTC-Kalendertag ausgewertet. Die Liga ist in der Container-Konfiguration
auf FotMob-Liga `54` (Bundesliga) voreingestellt; die UI erhält keinen weiteren
Filter, damit der Ladeauftrag eindeutig bleibt.

## Persistenzvertrag

- `fotmob_daily_index` speichert pro ausgewähltem Spieltag Datum, FotMob-ID,
  Liga-ID/-Name, Länder-Code/-Name, Saison, UTC-Anstoß, Teams und Status.
- `fotmob_daily_load_runs` speichert jeden angeforderten Tag, auch wenn an dem
  Tag kein Spiel gefunden wurde.
- `fotmob_match_index` bleibt die kleine Queue-/Retry-Steuerung.
- Matchdetails werden kanonisch und deterministisch unter
  `fotmob/match_core`, `period_stats`, `shots` und `events` als Parquet
  gespeichert. Der SQLite-Tagesindex enthält keine großen Statistik-Historien.
- Tipico-Snapshots schreiben zusätzlich das getrennte
  `tipico/strategy`-Dataset mit Quote-Provenienz, P0/P1/P2+, P1-Break-even,
  P1-Puffer, ROI und Einsatzwerten.

## Echter Smoke-Test

Ausgeführt mit aktiviertem manuellen Modus:

```text
from_date:       2025-08-22
to_date:         2025-08-22
league:          54 / Bundesliga / GER / Deutschland
season:          26891 / 2025/2026
fixtures:        1
detail requests: 1
HTTP:            3 x 200, 0 Fehler, 0 Retries, 0 x 429
canonical rows:  1 Core, 111 Period-Stats, 31 Shots, 26 Events
```

Der identische zweite Lauf war ebenfalls erfolgreich und übersprang das frisch
gespeicherte Detail (`requested=1`, `skipped=1`). Es wurde keine zweite Core-,
Period-, Shot- oder Event-Datei erzeugt.

## Container

`deploy/tipico-observer.env.example` setzt für die Dashboard-Datumsauswahl:

```text
FOTMOB_ENABLED=true
FOTMOB_HISTORY_ENABLED=true
FOTMOB_NETWORK_MODE=manual
STORE_FOTMOB_HISTORICAL_RAW=false
FOTMOB_ARCHIVE_ROOT=/var/lib/wetten/archive/fotmob
FOTMOB_HT_ENRICHMENT_ENABLED=true
```

Der separate `wetten-fotmob.service` bleibt deaktiviert. Damit läuft kein
permanenter FotMob-Poller; ein Netzwerkabruf entsteht nur durch den expliziten
Datumslauf in der UI oder den entsprechenden CLI-Befehl.

## Abgrenzung

Der vollständige historische Bundesliga-Backfill über alle bekannten Fixtures
wurde in dieser Validierung nicht automatisch gestartet. Er kann nach Prüfung
der lokalen Providerfreigabe bewusst über aufeinanderfolgende Datumsbereiche
ausgeführt und wegen des deterministischen Index-/Parquet-Schlüssels sicher
fortgesetzt werden.
