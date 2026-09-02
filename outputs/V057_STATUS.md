# V0.5.7 – Status

```text
V057_STATUS = PASS
```

The implementation and automated validation are complete. The selected-match
canary was verified against the live Parma–Cremonese event using FotMob ID
`6003655`; the response contained detailed statistics and did not change the
SQLite/Parquet live-storage surface.

| Check | Status | Evidence |
|---|---|---|
| `FOTMOB_LIVE_PANEL` | PASS | Separate selected-match tab with ON/OFF, Auto Refresh and Refresh now controls |
| `SELECTED_MATCH_ONLY` | PASS | No selected event or no accepted link means zero provider requests |
| `MANUAL_SELECTED_MATCH_BINDING` | PASS | An explicitly supplied FotMob ID or URL is validated for the selected event and kept in RAM only |
| `AUTO_REFRESH_10S` | PASS | Default setting is 10 seconds; the live panel creates one selected-event refresh cycle |
| `IN_MEMORY_CACHE` | PASS | Provider-ID cache with 8-second TTL; no disk-backed cache |
| `NO_LIVE_PERSISTENCE` | PASS | Live refresh stores only normalized RAM data; SQLite/Parquet regression test is green |
| `DETAILED_DATA_DETECTION` | PASS | 3-of-5 paired core metrics rule; xG is not required |
| `NO_DATA_FALLBACK` | PASS | Compact no-data message; no artificial zeros or live analysis table |
| `NO_DATA_STOPS_REFRESH` | PASS | Terminal `NO_DATA` state prevents subsequent requests and turns Auto Refresh off |
| `EARLY_MATCH_PENDING_STATE` | PASS | Empty stats remain `DETAILED_DATA_PENDING` in the early phase / before threshold |
| `PERIOD_VIEW` | PASS | Provider `All`, `FirstHalf` and `SecondHalf` stay separate |
| `LAST15_FEATURES` | PASS | Volatile shotmap aggregation for shots, shots on target and xG |
| `FINISHED_MATCH_STOP` | PASS | `FINISHED` retains the last display and blocks further requests |
| `COUNTRY_CODE_NORMALIZATION` | PASS | FotMob ISO country codes such as `ITA` match Tipico's localized country names |
| `CURRENT_LIVE_CLOCK` | PASS | Current `header.status.liveTime` phase/minute is parsed for the live panel |

## Verification

- `pytest -q`: **94 passed, 1 skipped**
- V0.5.7 live-service tests: **20 passed**
- Local Streamlit smoke test: Tipico live feed online, the current
  `Parma Calcio – US Cremonese` event opened, FotMob ID `6003655` was
  validated, and the live statistics table rendered without browser-console
  errors.
- Isolated real `matchDetails` request for FotMob ID `6003655`: HTTP 200,
  successful normalization of detailed stats and live clock; SQLite row
  counts and Parquet file set remained unchanged.
- `LIVE_REQUESTS_PER_SELECTED_MATCH_PER_MINUTE ≈ 6` at the default 10-second
  cadence; cache hits prevent duplicate requests caused by Streamlit reruns.

The existing Historical-/Halftime-Collector, canonical `match_core`,
`period_stats`, `shots`, `events`, target generation and
`SKIPPED_NO_HALFTIME` logic were not changed by V0.5.7.
