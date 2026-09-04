# V0.6.1.1 Status

Stand: 2026-09-04T14:08:45.359163+00:00

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

## Evidence

- Runtime report: `C:\Users\chris\Documents\Codex\2026-08-29\es-x20\V0611_RUNTIME_REPORT.md`
- Deployment status: `{"current_tree_hash": null, "integrity_checked": false, "manifest": null, "manifest_path": "C:\\Users\\chris\\Documents\\Codex\\2026-08-29\\es-x20\\DEPLOYMENT_MANIFEST.json", "manifest_present": false, "status": "MISSING", "tree_hash_match": null}`
- Lock status: `{"code_commit": null, "dataset_hash": null, "lock_created_at": null, "locked": false, "metadata": null, "mode": null, "owner": null, "owner_alive": false, "owner_command": null, "owner_hostname": null, "owner_identity_match": false, "owner_pid": null, "owner_ppid": null, "owner_process_alive": false, "owner_process_start_time": null, "owner_started_at": null, "path": "C:\\Users\\chris\\Documents\\Codex\\2026-08-29\\es-x20\\research\\runtime\\ml_run.lock.json", "phase": null, "process_start_time": null, "reason": "lock file absent", "requested_experiments": null, "run_id": null, "status": "UNLOCKED"}`
- CT110 canary evidence: `{"dataset_hash": "2f9e5f06f2d79116177985446b92ce116bf2de31b6cb48db96b6bf7317eaa488", "message": "no passed real-data CatBoost canary recorded", "passed_count": 0, "records": [], "scope": "CT110", "status": "PENDING", "tree_hash": "c10c2c9fc35f4972cbc357b121c0ce30373d424a7220aaee2ffbf699ab9c1bf6"}`
- Registry records: `10`; historical fallback identities: `['L04_CATBOOST_MULTICLASS_CORE', 'L05_BOOSTING_MULTICLASS_CORE_XG', 'L06_BOOSTING_MULTICLASS_CORE_SHOTMAP', 'L08_BOOSTING_BINARY_P1_CORE']`; current mismatches: `[]`.
- CT110 feed reconciliation, collector health and live FotMob canary are intentionally `PENDING` until container evidence exists.
