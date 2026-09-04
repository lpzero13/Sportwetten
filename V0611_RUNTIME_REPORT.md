# V0.6.1.1 Runtime Report

Production hardening evidence. Live CT110 values remain pending until observed in the container.

## Identity

- app_version: `0.5.9.1`
- research_version: `0.6.1.1`
- source_commit: `da7cb2b630f49b39aa64cf27bfadc637ab4a2cc9`
- deployed_commit: `NOT_OBSERVED`
- deployed_tree_hash: `NOT_OBSERVED`
- deployed_at: `NOT_OBSERVED`
- installer_version: `NOT_OBSERVED`
- deployment_manifest: `False`
- dataset_hash: `2f9e5f06f2d79116177985446b92ce116bf2de31b6cb48db96b6bf7317eaa488`

## Runtime gates

```text
V0611_STATUS = PARTIAL
V0611_VERSION = 0.6.1.1
ML_LOCK_DIAGNOSTICS = PASS
ML_STALE_LOCK_DETECTION = PASS
ML_SAFE_LOCK_RECOVERY = PASS
CATBOOST_RUNTIME = PASS
CATBOOST_CT_REAL_DATA_CANARY = PENDING
NO_MODEL_FALLBACK = PASS
MODEL_IDENTITY = PASS
FEED_RECONCILIATION = PASS
FEED_RESTART_TEST = PASS
FEED_FAILURE_INJECTION_TEST = PASS
FEED_LIVE_CANARY = PENDING
FEED_RECONCILIATION_UNIT = PASS
FEED_RECONCILIATION_LIVE = PENDING
STATUS_PHASE_PROFILING = PASS
FAST_HEARTBEAT = PASS
FULL_STATUS_CACHE = PASS
STATUS_P95_TARGET = PASS
STATUS_HEARTBEAT = PASS
STATUS_FULL_CACHED = PASS
DEPLOYMENT_MANIFEST = PENDING
DEPLOYED_COMMIT_IDENTITY = PENDING
DEPLOYED_TREE_HASH = PENDING
DEPLOYMENT_INTEGRITY = NOT_CHECKED
DISK_GUARD = PASS
STORAGE_DISK_GUARD = PASS
OUTBOX_HEALTH = PASS
OUTBOX_OBSERVABILITY = PASS
FOTMOB_SHARED_INDEX_CACHE = PASS
FOTMOB_SHARED_DAILY_INDEX_CACHE = PASS
FOTMOB_NEGATIVE_CACHE = PASS
FOTMOB_NEGATIVE_CACHE_LIVE_CANARY = PENDING
CONFIRMED_LINK_FAST_PATH = PASS
FOTMOB_LIVE_CANARY = PENDING
COLLECTOR_HEALTH = PENDING
FULL_TEST_SUITE = PASS
CURRENT_RUN_MODEL_IDENTITY_MISMATCHES = 0
HISTORICAL_MODEL_IDENTITY_MISMATCHES = 4
CT110_DEPLOYMENT = PENDING
CT110_RUNTIME_CANARY = PENDING
DEEP_RUN_READY = NO
```

## Status benchmark

```json
{
  "status": "PASS",
  "heartbeat": {
    "iterations": 100,
    "median_ms": 0.04369998350739479,
    "p95_ms": 0.0543000060133636,
    "max_ms": 0.07619999814778566,
    "target_p95_ms": 100.0,
    "status": "PASS"
  },
  "full_cached": {
    "iterations": 100,
    "median_ms": 0.2953500079456717,
    "p95_ms": 0.3341999836266041,
    "max_ms": 0.4928000271320343,
    "target_p95_ms": 500.0,
    "status": "PASS"
  },
  "full_uncached_sample": {
    "iterations": 10,
    "median_ms": 0.6891000084578991,
    "p95_ms": 0.7465999806299806,
    "max_ms": 1.1823000386357307,
    "target_p95_ms": 500.0,
    "status": "PASS"
  },
  "status_generation_breakdown": {
    "queue_metrics_ms": 0.007,
    "feed_metrics_ms": 0.025,
    "snapshot_metrics_ms": 0.001,
    "database_metrics_ms": 0.097,
    "archive_metrics_ms": 0.002,
    "feature_matrix_ms": 0.009,
    "fotmob_metrics_ms": 0.0,
    "outbox_metrics_ms": 0.016,
    "strategy_metrics_ms": 0.001,
    "storage_metrics_ms": 0.064,
    "runtime_identity_ms": 0.0,
    "research_metrics_ms": 0.0,
    "json_serialize_ms": 0.0,
    "file_write_ms": 0.0,
    "other_ms": 0.167,
    "total_ms": 0.391
  },
  "root": "C:\\Users\\chris\\Documents\\Codex\\2026-08-29\\es-x20",
  "source_tree_hash": "c10c2c9fc35f4972cbc357b121c0ce30373d424a7220aaee2ffbf699ab9c1bf6",
  "generated_at": "2026-09-04T14:08:00.595220+00:00"
}
```

## Feed reconciliation

- last_5m_rejects: `NOT_OBSERVED`
- last_15m_rejects: `NOT_OBSERVED`
- last_60m_rejects: `NOT_OBSERVED`
- reconciliations: `NOT_OBSERVED`
- stale_reconciliations: `NOT_OBSERVED`

## Status performance

- fast median/p95/max ms: `0.04369998350739479` / `0.0543000060133636` / `0.07619999814778566`
- full median/p95/max ms: `0.2953500079456717` / `0.3341999836266041` / `0.4928000271320343`
- slowest subphase: `total_ms`

## FotMob resolver

- network index requests: `NOT_OBSERVED`
- cache hits: `NOT_OBSERVED`
- cache misses: `NOT_OBSERVED`
- negative cache hits: `NOT_OBSERVED`
- full resolver attempts: `NOT_OBSERVED`
- confirmed fast path hits: `NOT_OBSERVED`
- unique eligible/linked/unmatched: `NOT_OBSERVED` / `NOT_OBSERVED` / `NOT_OBSERVED`

## ML canary

- lock status: `UNLOCKED`
- CatBoost version: `1.2.8`
- canary requested/effective: `CATBOOST` / `CATBOOST` when status is PASS; current status `PENDING`
- artifact reload/prediction validation: `PENDING`

## Resolver and feed evidence

- The shared daily-index cache remains the source of resolver candidates.
- Negative cache entries are keyed by internal event and resolver-input fingerprint; a daily generation bump alone does not invalidate them.
- One missing soccer event does not terminalize it; only the conservative reconciliation gate may persist `NO_LONGER_LIVE`.
- No live/provider value is inferred by this local report.
