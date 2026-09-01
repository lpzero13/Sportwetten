# V0.5.6 FotMob Performance Report

- Status: **PASS**
- Run: `v056-20260901T033800Z-c1275170`
- Completed days: `2026-08-26 .. 2026-08-28`
- Detail IDs available/tested: `502` / `25`

## Effective configuration

| Setting | Value |
|---|---:|
| `rate_mode` | `ADAPTIVE` |
| `initial_rps` | `5.0` |
| `rps_step` | `5.0` |
| `max_rps` | `30.0` |
| `initial_workers` | `10` |
| `max_workers` | `40` |
| `rate_window_requests` | `20` |
| `requests_per_level` | `25` |

## Probe stages

`Requests` counts actual HTTP attempts; retries are reported separately and `successful matches` counts completed detail calls.

| Phase | RPS target | Workers | Requests | Successful | Success rate | 429 | 403 | 5xx | Timeouts | Retries | Median ms | P95 ms | Effective RPS | Matches/min | MB/min | Status |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| RPS | 5.00 | 10 | 25 | 25 | 100.0% | 0 | 0 | 0 | 0 | 0 | 25 | 77 | 5.06 | 303.77 | 50.57 | STABLE |
| RPS | 10.00 | 10 | 25 | 25 | 100.0% | 0 | 0 | 0 | 0 | 0 | 19 | 28 | 9.47 | 567.97 | 94.55 | STABLE |
| RPS | 15.00 | 20 | 25 | 25 | 100.0% | 0 | 0 | 0 | 0 | 0 | 15 | 24 | 13.23 | 793.65 | 132.12 | STABLE |
| RPS | 20.00 | 30 | 25 | 25 | 100.0% | 0 | 0 | 0 | 0 | 0 | 15 | 26 | 16.49 | 989.45 | 164.72 | STABLE |
| RPS | 25.00 | 30 | 25 | 25 | 100.0% | 0 | 0 | 0 | 0 | 0 | 16 | 28 | 21.63 | 1297.58 | 216.01 | STABLE |
| RPS | 30.00 | 40 | 25 | 25 | 100.0% | 0 | 0 | 0 | 0 | 0 | 15 | 25 | 21.91 | 1314.64 | 218.85 | STABLE |
| WORKER | 30.00 | 10 | 25 | 25 | 100.0% | 0 | 0 | 0 | 0 | 0 | 16 | 29 | 21.93 | 1315.79 | 219.04 | STABLE |
| WORKER | 30.00 | 20 | 25 | 25 | 100.0% | 0 | 0 | 0 | 0 | 0 | 15 | 26 | 21.63 | 1297.58 | 216.01 | STABLE |
| WORKER | 30.00 | 30 | 25 | 25 | 100.0% | 0 | 0 | 0 | 0 | 0 | 16 | 29 | 21.91 | 1314.64 | 218.85 | STABLE |
| WORKER | 30.00 | 40 | 25 | 25 | 100.0% | 0 | 0 | 0 | 0 | 0 | 16 | 24 | 21.91 | 1314.64 | 218.85 | STABLE |

## Decision

- `MAX_TESTED_RPS = 30.0`
- `MAX_STABLE_RPS = 30.0`
- `KNOWN_STABLE_MAX_RPS = None`
- `RECOMMENDED_RPS = 30.0`
- `RECOMMENDED_WORKERS = 10`
- `BOTTLENECK = NO_MATERIAL_WORKER_SCALING`

429/403 responses and parse failures are treated as provider instability. `SKIPPED_NO_HALFTIME` is a data-availability outcome and is not part of the performance-health decision.
