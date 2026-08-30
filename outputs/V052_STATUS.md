# V0.5.2 Status – FotMob Historical Bundesliga Foundation

Stand: 30.08.2026, Europe/Berlin

```text
FOTMOB_V052_STATUS = PARTIAL
FOTMOB_PROVIDER_DECISION = LIMITED_USE
AUTOMATED_USAGE = UNCLEAR
FOTMOB_EXTERNAL_DISCOVERY = NOT_RUN_BY_POLICY
FOTMOB_EXTERNAL_DETAILS = NOT_RUN_BY_POLICY
```

## Abnahme

| Kriterium | Status | Beleg |
|---|---|---|
| League-/Season-Discovery ohne ID-Brute-Force | PASS (offline) | `fotmob/history_discovery.py` |
| echte Season-ID persistieren | PASS (Implementierung) | `fotmob_seasons` |
| Fixture-/Results-Index mit Provider-ID | PASS (offline) | `fotmob_match_index` |
| deterministische 5er-Sample pro Season | PASS (offline) | `fotmob_history_samples` |
| explizite HT-/FT-Normalisierung | PASS | `fotmob/history_models.py` + Parser |
| Queue, Retry, Worker, stale-Recovery, Resume | PASS (offline) | `fotmob/history_storage.py` |
| zstd-Parquet plus Archive-Dedupe | PASS (offline) | `FotMobHistoricalArchive` |
| echte 2025/26-Discoveryantwort | NOT_RUN_BY_POLICY | keine externe Antwort behauptet |
| fünf echte Details 2025/26 | NOT_RUN_BY_POLICY | keine IDs/Stats erfunden |
| fünf echte Details 2024/25 | NOT_RUN_BY_POLICY | keine IDs/Stats erfunden |

## Interpretation

Die technische Foundation ist fertig und durch lokale synthetische Fixtures
abgedeckt. Der V0.5.2-Gesamtstatus kann trotzdem nicht `PASS` sein, weil die
Spec für den PASS zehn echte Detailantworten aus zwei Saisons verlangt.
FotMob weist auf der öffentlichen Seite darauf hin, dass automatische,
systematische oder regelmäßige Nutzung nicht erlaubt ist:
[FotMob](https://www.fotmob.com/de). Die V0.5.1-Providerentscheidung bleibt
deshalb `LIMITED_USE` bei `AUTOMATED_USAGE=UNCLEAR`; die Historical-CLI stoppt
vor dem Netzwerkzugriff.

## Fertiggestellt

- Unified CLI: `scripts/fotmob_history.py`
- Discovery-Wrapper: `scripts/discover_fotmob_league.py`
- Index-Wrapper: `scripts/index_fotmob_matches.py`
- Detailscan-Wrapper: `scripts/scan_fotmob_history.py`
- Offline-Fixture- und End-to-End-Tests in `tests/test_fotmob_history.py`
- SQLite-Katalog, resumierbare Queue und Parquet-Archiv

Eine spätere externe Abnahme darf erst nach einer neuen, ausdrücklich
zulässigen Providerfreigabe erfolgen. Dann sind beide Season-IDs aus der
Discoveryantwort zu übernehmen, je Season mindestens fünf abgeschlossene
Matches deterministisch zu sampeln und die tatsächlichen Parser-/HTTP-
Metriken in diesem Report zu ergänzen.
