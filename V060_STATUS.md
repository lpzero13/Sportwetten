# V0.6.0 Status

Stand: 2026-09-04T13:44:43.381914+00:00

```text
V060_STATUS = PASS
TARGET_H2_ONLY = PASS
EXTRA_TIME_PROTECTION = PASS
LEAKAGE_AUDIT = PASS
DATASET_BUILDER = PASS
DATASET_CACHE = PASS
LOCAL_EXPERIMENT_LIMIT_10 = PASS
NO_AUTO_TRAINING = PASS
EXPERIMENT_PLANNER = PASS
EXPERIMENT_REGISTRY = PASS
EXPERIMENT_DEDUP = PASS
RESUME = PASS
SCALABLE_MAX_EXPERIMENTS = PASS
BREADTH_FIRST_SEARCH = PASS
MULTICLASS = PASS
BINARY_P1 = PASS
COUNT_MODEL = PASS
ENSEMBLE = PASS
TIME_SPLIT = PASS
WALK_FORWARD = PASS
LOCKED_TEST = PASS
CALIBRATION = PASS
P1_ANALYSIS = PASS
LOCAL_10_MODELS = PASS
FULL_TEST_SUITE = PASS
```

## Local evidence

- Registry counts: `{"COMPLETED": 10}`
- Planner scalability probe: `{"generated": 100, "status": "PASS", "unique": 100}`
- Dataset: `49802` matches, `48745` eligible, hash `2f9e5f06f2d79116177985446b92ce116bf2de31b6cb48db96b6bf7317eaa488`
- Date range: `{'from': '2024-08-31', 'to': '2026-09-01'}`
- Target distribution: `{'H2_GOALS_0': 10558, 'H2_GOALS_1': 15925, 'H2_GOALS_2_PLUS': 22262}`
- Archive workers: requested `10`, RAM-guarded effective `10`.

## Local model table

| # | Model | Target | Features | Train N | Validation N | Test N | Coverage | P1 Brier | Calibration | LogLoss |
| - | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- | ---: |
| 1 | LOGISTIC (`L01_LOGISTIC_MULTICLASS_SCORE_ONLY`) | MULTICLASS | SCORE_ONLY | 38996 | 23397 | 0 | 0.4800 | 0.2180 | yes | 1.0526 |
| 2 | LOGISTIC (`L02_LOGISTIC_MULTICLASS_CORE`) | MULTICLASS | CORE | 38996 | 23397 | 0 | 0.4800 | 0.2188 | yes | 1.0461 |
| 3 | HISTGRADIENTBOOSTING (`L03_HISTGRADIENTBOOSTING_MULTICLASS_CORE`) | MULTICLASS | CORE | 38996 | 23397 | 0 | 0.4800 | 0.2273 | yes | 1.0908 |
| 4 | CATBOOST (`L04_CATBOOST_MULTICLASS_CORE`) | MULTICLASS | CORE | 38996 | 23397 | 0 | 0.4800 | 0.2943 | yes | 1.8377 |
| 5 | CATBOOST (`L05_BOOSTING_MULTICLASS_CORE_XG`) | MULTICLASS | CORE_XG | 21758 | 13053 | 0 | 0.4799 | 0.2998 | yes | 1.8564 |
| 6 | CATBOOST (`L06_BOOSTING_MULTICLASS_CORE_SHOTMAP`) | MULTICLASS | CORE_SHOTMAP | 21749 | 13047 | 0 | 0.4799 | 0.2869 | yes | 1.7127 |
| 7 | EXTRATREES (`L07_EXTRATREES_MULTICLASS_CORE`) | MULTICLASS | CORE | 38996 | 23397 | 0 | 0.4800 | 0.2943 | yes | 1.8377 |
| 8 | CATBOOST (`L08_BOOSTING_BINARY_P1_CORE`) | BINARY_P1 | CORE | 38996 | 23397 | 0 | 0.4800 | 0.2762 | yes | 1.4116 |
| 9 | POISSON (`L09_POISSON_COUNT_CORE`) | COUNT | CORE | 38996 | 23397 | 0 | 0.4800 | 0.2184 | no | 1.0535 |
| 10 | ENSEMBLE (`L10_ENSEMBLE_P1_OOF`) | MULTICLASS | CORE | — | 23397 | 0 | 0.6000 | 0.2181 | no | 1.0529 |

## Safety and scope

- Target is only regulation-time goals after halftime; H2_GOALS_1 is LOSS_MIDDLE.
- Extra-time-ambiguous matches are excluded, not estimated.
- Feature metadata and the hard leakage audit enforce MODEL_CUTOFF=HALFTIME.
- Research reads canonical source Parquet; it writes only research cache, registry, artifacts, reports and predictions.
- No automatic training is wired into installation, systemd, the collector or application startup.
- No model deployment, Paper Trading change, Tipico strategy change or ROI claim is made by V0.6.0.

## Limitations

The report is local research evidence. A CT110 run is a separate explicit process and must use its own dataset hash, registry and runtime identity.
