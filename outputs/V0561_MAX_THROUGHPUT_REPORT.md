# V0.5.6.1 FotMob Max-Throughput & Bottleneck Report

- Status: **PASS**
- Run: `v0561-20260901T111203Z-2f2ae299`
- Exact completed days: `2026-08-26 .. 2026-08-28`
- Detail IDs available/tested: `502` / `100`
- Critical highest-stable sample: `250` detail requests
- Promotion candidate after validation: `FOTMOB_MAX_RPS=100.0` (now used as the standard ceiling)

## Probe configuration

| Setting | Value |
|---|---:|
| `configured_max_rps` | `30.0` |
| `temporary_max_rps` | `100.0` |
| `initial_workers` | `10` |
| `max_workers` | `40` |
| `connection_pool_size` | `40` |
| `requests_per_level` | `100` |
| `critical_requests` | `250` |
| `worker_levels` | `[10, 20, 30, 40]` |

## Required stage table

`Requests` are actual HTTP attempts; retries remain visible in their own column. STABLE requires at least 99.5% success, zero 429/403/parse failures, and very low transient failure rates.

| Phase | Target RPS | Effective RPS | Workers | Requests | Success % | 429 | 403 | 5xx | Timeouts | Retries | Median ms | P95 ms | Matches/min | Status |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| RPS | 30.00 | 21.48 | 10 | 100 | 100.00% | 0 | 0 | 0 | 0 | 0 | 18 | 83 | 1288.66 | STABLE |
| RPS | 35.00 | 32.00 | 10 | 100 | 100.00% | 0 | 0 | 0 | 0 | 0 | 17 | 28 | 1920.00 | STABLE |
| RPS | 40.00 | 31.84 | 10 | 100 | 100.00% | 0 | 0 | 0 | 0 | 0 | 17 | 30 | 1910.22 | STABLE |
| RPS | 45.00 | 32.15 | 10 | 100 | 100.00% | 0 | 0 | 0 | 0 | 0 | 16 | 25 | 1929.26 | STABLE |
| RPS | 50.00 | 32.15 | 10 | 100 | 100.00% | 0 | 0 | 0 | 0 | 0 | 18 | 27 | 1929.26 | STABLE |
| RPS | 55.00 | 32.15 | 10 | 100 | 100.00% | 0 | 0 | 0 | 0 | 0 | 16 | 27 | 1929.26 | STABLE |
| RPS | 60.00 | 32.15 | 10 | 100 | 100.00% | 0 | 0 | 0 | 0 | 0 | 17 | 27 | 1929.26 | STABLE |
| RPS | 70.00 | 63.37 | 10 | 100 | 100.00% | 0 | 0 | 0 | 0 | 0 | 19 | 29 | 3802.28 | STABLE |
| RPS | 80.00 | 63.37 | 10 | 100 | 100.00% | 0 | 0 | 0 | 0 | 0 | 18 | 27 | 3802.28 | STABLE |
| RPS | 90.00 | 61.54 | 10 | 100 | 100.00% | 0 | 0 | 0 | 0 | 0 | 18 | 28 | 3692.31 | STABLE |
| RPS | 100.00 | 62.74 | 10 | 100 | 100.00% | 0 | 0 | 0 | 0 | 0 | 17 | 30 | 3764.12 | STABLE |
| CRITICAL_STABLE | 100.00 | 63.24 | 10 | 250 | 100.00% | 0 | 0 | 0 | 0 | 0 | 18 | 27 | 3794.59 | STABLE |
| WORKER | 100.00 | 62.74 | 10 | 100 | 100.00% | 0 | 0 | 0 | 0 | 0 | 17 | 29 | 3764.12 | STABLE |
| WORKER | 100.00 | 60.94 | 20 | 100 | 100.00% | 0 | 0 | 0 | 0 | 0 | 18 | 34 | 3656.31 | STABLE |
| WORKER | 100.00 | 63.37 | 30 | 100 | 100.00% | 0 | 0 | 0 | 0 | 0 | 17 | 27 | 3802.28 | STABLE |
| WORKER | 100.00 | 62.11 | 40 | 100 | 100.00% | 0 | 0 | 0 | 0 | 0 | 18 | 31 | 3726.71 | STABLE |

## Decision summary

MAX_TESTED_TARGET_RPS = 100.0
MAX_STABLE_TARGET_RPS = 100.0
MAX_EFFECTIVE_RPS = 63.371356147844374
RECOMMENDED_RPS = 100.0
RECOMMENDED_WORKERS = 10
RECOMMENDED_CONNECTION_POOL = 40
BOTTLENECK = REQUEST_SCHEDULING

## Internal path profile

The detail probe bypasses SQLite/Parquet writes by design, so those two components are not declared bottlenecks from this run. The request path is: global rate-controller reservation -> ThreadPoolExecutor worker -> shared requests.Session/HTTPAdapter -> provider response -> parser.

- Classification: `REQUEST_SCHEDULING`
- Evidence: The fixed controller target was active, but OS/worker scheduling delivered quantized rate slots below target.
- Highest RPS stage: target `100.00`, controller `100.00`, reserved slots `63.08/s`, HTTP starts `63.08/s`.
- Highest RPS timings: HTTP median/P95 `17/30 ms`, detail-call median/P95 `49/589 ms`, parser median/P95 `4/6 ms`.
- Connection pool decision: SKIPPED — Keine Evidenz, dass mehr als 40 simultane Verbindungen technisch benötigt werden; ein künstlicher Pool-Vergleich würde die Messung nicht verbessern.

## Backfill runtime estimates

Based on recommended effective throughput `62.74` detail matches/s:

| Detail matches | Estimated runtime |
|---:|---:|
| 1000 | 15.9 s |
| 3000 | 47.8 s |
| 10000 | 2.7 min |
| 25000 | 6.6 min |
| 50000 | 13.3 min |
