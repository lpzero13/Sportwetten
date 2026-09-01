# V0.5.6 Status

V056_STATUS = PASS
CAPABILITY_AUDIT = PASS
LEGACY_COLLECTOR_REVIEW = PASS
ADAPTIVE_RATE_CONTROL = PASS
PERFORMANCE_PROBE = PASS
MAX_STABLE_RPS = 30.0
RECOMMENDED_RPS = 30.0
RECOMMENDED_WORKERS = 10

Run: `v056-20260901T033800Z-c1275170`
Days: `2026-08-26 .. 2026-08-28`
Bottleneck: `NO_MATERIAL_WORKER_SCALING`

The first probe attempt was intentionally retained as an unstable diagnostic
profile: it exposed an undecoded Brotli response. The client now advertises
only gzip/deflate, and the successful rerun is the authoritative result in
`V056_FOTMOB_PERFORMANCE_REPORT.md`.
