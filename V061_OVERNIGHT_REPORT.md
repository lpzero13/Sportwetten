# V0.6.1 Overnight Report

Research-only evidence. No model is deployed and no historical Tipico ROI is claimed.

## Research run summary

- run_id: `MULTIPLE_OR_NOT_SET`
- start: `2026-09-03T14:12:27.733249+00:00`
- finish: `2026-09-03T14:27:44.623053+00:00`
- dataset_hash: `2f9e5f06f2d79116177985446b92ce116bf2de31b6cb48db96b6bf7317eaa488`
- code_commit: `289436870dbd257f9827081456c3c3120e620550`
- environment_hash: `486372e0fe0c3560ae0931598b97a93f076891a4d7fdb2e79eba5e3031c4d550`
- planned: `10`
- completed: `10`
- failed: `0`
- skipped: `0`
- interrupted: `0`

## Requested/effective model identity

- REQUESTED_EFFECTIVE_MISMATCHES = **0**
- RUN_VALID = **YES**
- requested counts: `{"CATBOOST": 4, "ENSEMBLE": 1, "EXTRATREES": 1, "HISTGRADIENTBOOSTING": 1, "LOGISTIC": 2, "POISSON": 1}`
- effective counts: `{"EXTRATREES": 1, "EXTRATREES_FALLBACK": 4, "HISTGRADIENTBOOSTING": 1, "LOGISTIC": 2, "OOF_AVERAGE": 1, "POISSON": 1}`

## Top development/validation models

| Model | Requested | Effective | Target | Features | Validation N | P1 Brier | Raw P1 Brier | LogLoss | Raw LogLoss | Calibration error |
| --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `L01_LOGISTIC_MULTICLASS_SCORE_ONLY` | LOGISTIC | LOGISTIC | MULTICLASS | SCORE_ONLY | 23397 | 0.21795229916628764 | — | 1.052594478076334 | — | 0.007752039110992592 |
| `L10_ENSEMBLE_P1_OOF` | ENSEMBLE | OOF_AVERAGE | MULTICLASS | CORE | 23397 | 0.2180900486765037 | — | 1.052862481313612 | — | 0.009727874628620459 |
| `L09_POISSON_COUNT_CORE` | POISSON | POISSON | COUNT | CORE | 23397 | 0.21840624875099363 | — | 1.053534020009347 | — | 0.01170371014624838 |
| `L02_LOGISTIC_MULTICLASS_CORE` | LOGISTIC | LOGISTIC | MULTICLASS | CORE | 23397 | 0.21877759677012848 | — | 1.0461464848549367 | — | 0.006429671040002638 |
| `L03_HISTGRADIENTBOOSTING_MULTICLASS_CORE` | HISTGRADIENTBOOSTING | HISTGRADIENTBOOSTING | MULTICLASS | CORE | 23397 | 0.22732790336170539 | — | 1.0907549853821001 | — | 0.0007934322955612805 |
| `L08_BOOSTING_BINARY_P1_CORE` | CATBOOST | EXTRATREES_FALLBACK | BINARY_P1 | CORE | 23397 | 0.2762468434252768 | — | 1.4115609514193472 | — | 0.18717237876971585 |
| `L06_BOOSTING_MULTICLASS_CORE_SHOTMAP` | CATBOOST | EXTRATREES_FALLBACK | MULTICLASS | CORE_SHOTMAP | 13047 | 0.2869418890857767 | — | 1.7126506379397373 | — | 0.0737236567493571 |
| `L04_CATBOOST_MULTICLASS_CORE` | CATBOOST | EXTRATREES_FALLBACK | MULTICLASS | CORE | 23397 | 0.29427225207043006 | — | 1.837709907279994 | — | 0.05594113598384853 |
| `L07_EXTRATREES_MULTICLASS_CORE` | EXTRATREES | EXTRATREES | MULTICLASS | CORE | 23397 | 0.29427225207043006 | — | 1.837709907279994 | — | 0.05594113598384853 |
| `L05_BOOSTING_MULTICLASS_CORE_XG` | CATBOOST | EXTRATREES_FALLBACK | MULTICLASS | CORE_XG | 13053 | 0.299828281235703 | — | 1.8563871658800948 | — | 0.058369794360078664 |

## Low-P1 groups and stability

Thresholds are validation-only. A strategy candidate is emitted only when its sample, fold count and calibration guard pass.

```json
[
  {
    "experiment_id": "L01_LOGISTIC_MULTICLASS_SCORE_ONLY",
    "feature_universe": "SCORE_ONLY",
    "thresholds": [
      {
        "actual_p1_rate": 0.2800429184549356,
        "coverage": 0.07966833354703594,
        "max_predicted_p1": 0.3,
        "mean_predicted_p1": 0.2875960168097512,
        "sample_n": 1864,
        "zero_or_2plus_ci_high": 0.7398728256326784,
        "zero_or_2plus_ci_low": 0.6991365967825013,
        "zero_or_2plus_hit_rate": 0.7199570815450643
      },
      {
        "actual_p1_rate": 0.11857707509881422,
        "coverage": 0.010813352139163141,
        "max_predicted_p1": 0.275,
        "mean_predicted_p1": 0.25026666038439743,
        "sample_n": 253,
        "zero_or_2plus_ci_high": 0.9156649991181705,
        "zero_or_2plus_ci_low": 0.8357713183913673,
        "zero_or_2plus_hit_rate": 0.8814229249011858
      },
      {
        "actual_p1_rate": 0.024096385542168676,
        "coverage": 0.003547463350002137,
        "max_predicted_p1": 0.25,
        "mean_predicted_p1": 0.21942114244133584,
        "sample_n": 83,
        "zero_or_2plus_ci_high": 0.9933668338592725,
        "zero_or_2plus_ci_low": 0.916336907565837,
        "zero_or_2plus_hit_rate": 0.9759036144578314
      },
      {
        "actual_p1_rate": 0.0,
        "coverage": 0.0019233235030132068,
        "max_predicted_p1": 0.225,
        "mean_predicted_p1": 0.2046711216026364,
        "sample_n": 45,
        "zero_or_2plus_ci_high": 1.0,
        "zero_or_2plus_ci_low": 0.9213484012671117,
        "zero_or_2plus_hit_rate": 1.0
      },
      {
        "actual_p1_rate": 0.0,
        "coverage": 0.0005128862674701885,
        "max_predicted_p1": 0.2,
        "mean_predicted_p1": 0.18005907461239182,
        "sample_n": 12,
        "zero_or_2plus_ci_high": 1.0,
        "zero_or_2plus_ci_low": 0.7575059933447592,
        "zero_or_2plus_hit_rate": 1.0
      },
      {
        "actual_p1_rate": 0.0,
        "coverage": 0.00021370261144591188,
        "max_predicted_p1": 0.175,
        "mean_predicted_p1": 0.16682839319469236,
        "sample_n": 5,
        "zero_or_2plus_ci_high": 1.0,
        "zero_or_2plus_ci_low": 0.5655175352168251,
        "zero_or_2plus_hit_rate": 1.0
      },
      {
        "actual_p1_rate": null,
        "coverage": 0.0,
        "max_predicted_p1": 0.15,
        "mean_predicted_p1": null,
        "sample_n": 0,
        "zero_or_2plus_ci_high": null,
        "zero_or_2plus_ci_low": null,
        "zero_or_2plus_hit_rate": null
      },
      {
        "actual_p1_rate": null,
        "coverage": 0.0,
        "max_predicted_p1": 0.125,
        "mean_predicted_p1": null,
        "sample_n": 0,
        "zero_or_2plus_ci_high": null,
        "zero_or_2plus_ci_low": null,
        "zero_or_2plus_hit_rate": null
      },
      {
        "actual_p1_rate": null,
        "coverage": 0.0,
        "max_predicted_p1": 0.1,
        "mean_predicted_p1": null,
        "sample_n": 0,
        "zero_or_2plus_ci_high": null,
        "zero_or_2plus_ci_low": null,
        "zero_or_2plus_hit_rate": null
      }
    ],
    "score_breakdown": {},
    "league_breakdown": {},
    "season_breakdown": {},
    "intersections": {},
    "extreme_calibration": {}
  },
  {
    "experiment_id": "L02_LOGISTIC_MULTICLASS_CORE",
    "feature_universe": "CORE",
    "thresholds": [
      {
        "actual_p1_rate": 0.3112814895947426,
        "coverage": 0.19511048425011754,
        "max_predicted_p1": 0.3,
        "mean_predicted_p1": 0.2691094923673892,
        "sample_n": 4565,
        "zero_or_2plus_ci_high": 0.7019866486191074,
        "zero_or_2plus_ci_low": 0.6751330250507993,
        "zero_or_2plus_hit_rate": 0.6887185104052574
      },
      {
        "actual_p1_rate": 0.2852233676975945,
        "coverage": 0.07462495191691243,
        "max_predicted_p1": 0.275,
        "mean_predicted_p1": 0.23663649328426248,
        "sample_n": 1746,
        "zero_or_2plus_ci_high": 0.7354660373163667,
        "zero_or_2plus_ci_low": 0.6931442211841164,
        "zero_or_2plus_hit_rate": 0.7147766323024055
      },
      {
        "actual_p1_rate": 0.23863636363636365,
        "coverage": 0.03008932769158439,
        "max_predicted_p1": 0.25,
        "mean_predicted_p1": 0.19518422429573706,
        "sample_n": 704,
        "zero_or_2plus_ci_high": 0.7913783058732328,
        "zero_or_2plus_ci_low": 0.7285121235643278,
        "zero_or_2plus_hit_rate": 0.7613636363636364
      },
      {
        "actual_p1_rate": 0.16071428571428573,
        "coverage": 0.014360815489165278,
        "max_predicted_p1": 0.225,
        "mean_predicted_p1": 0.14637050701049825,
        "sample_n": 336,
        "zero_or_2plus_ci_high": 0.8746858232494936,
        "zero_or_2plus_ci_low": 0.7962152515963994,
        "zero_or_2plus_hit_rate": 0.8392857142857143
      },
      {
        "actual_p1_rate": 0.08225108225108226,
        "coverage": 0.009873060648801129,
        "max_predicted_p1": 0.2,
        "mean_predicted_p1": 0.11526498810999651,
        "sample_n": 231,
        "zero_or_2plus_ci_high": 0.946713113305677,
        "zero_or_2plus_ci_low": 0.8751179252562113,
        "zero_or_2plus_hit_rate": 0.9177489177489178
      },
      {
        "actual_p1_rate": 0.059782608695652176,
        "coverage": 0.007864256101209556,
        "max_predicted_p1": 0.175,
        "mean_predicted_p1": 0.09656961429705677,
        "sample_n": 184,
        "zero_or_2plus_ci_high": 0.9662938390687563,
        "zero_or_2plus_ci_low": 0.8961355788939664,
        "zero_or_2plus_hit_rate": 0.9402173913043478
      },
      {
        "actual_p1_rate": 0.033783783783783786,
        "coverage": 0.006325597298798991,
        "max_predicted_p1": 0.15,
        "mean_predicted_p1": 0.07970015675376224,
        "sample_n": 148,
        "zero_or_2plus_ci_high": 0.9854849050136472,
        "zero_or_2plus_ci_low": 0.9233577857231483,
        "zero_or_2plus_hit_rate": 0.9662162162162162
      },
      {
        "actual_p1_rate": 0.04201680672268908,
        "coverage": 0.0050861221524127025,
        "max_predicted_p1": 0.125,
        "mean_predicted_p1": 0.06582074304364172,
        "sample_n": 119,
        "zero_or_2plus_ci_high": 0.9819215255403342,
        "zero_or_2plus_ci_low": 0.9054010530615868,
        "zero_or_2plus_hit_rate": 0.957983193277311
      },
      {
        "actual_p1_rate": 0.05813953488372093,
        "coverage": 0.003675684916869684,
        "max_predicted_p1": 0.1,
        "mean_predicted_p1": 0.04896834777185976,
        "sample_n": 86,
        "zero_or_2plus_ci_high": 0.9749130643789203,
        "zero_or_2plus_ci_low": 0.8710215519732145,
        "zero_or_2plus_hit_rate": 0.9418604651162791
      }
    ],
    "score_breakdown": {},
    "league_breakdown": {},
    "season_breakdown": {},
    "intersections": {},
    "extreme_calibration": {}
  },
  {
    "experiment_id": "L03_HISTGRADIENTBOOSTING_MULTICLASS_CORE",
    "feature_universe": "CORE",
    "thresholds": [
      {
        "actual_p1_rate": 0.30697316322041357,
        "coverage": 0.38859682865324613,
        "max_predicted_p1": 0.3,
        "mean_predicted_p1": 0.209763188698587,
        "sample_n": 9092,
        "zero_or_2plus_ci_high": 0.7024244337739718,
        "zero_or_2plus_ci_low": 0.6834661972237988,
        "zero_or_2plus_hit_rate": 0.6930268367795864
      },
      {
        "actual_p1_rate": 0.29995781184081,
        "coverage": 0.30392785399837585,
        "max_predicted_p1": 0.275,
        "mean_predicted_p1": 0.18800460328889634,
        "sample_n": 7111,
        "zero_or_2plus_ci_high": 0.710582474536097,
        "zero_or_2plus_ci_low": 0.6892858874599223,
        "zero_or_2plus_hit_rate": 0.70004218815919
      },
      {
        "actual_p1_rate": 0.2994121969140338,
        "coverage": 0.23267940334230885,
        "max_predicted_p1": 0.25,
        "mean_predicted_p1": 0.1650576096606783,
        "sample_n": 5444,
        "zero_or_2plus_ci_high": 0.712609097185078,
        "zero_or_2plus_ci_low": 0.688283626384153,
        "zero_or_2plus_hit_rate": 0.7005878030859662
      },
      {
        "actual_p1_rate": 0.2963500120860527,
        "coverage": 0.17681754071034747,
        "max_predicted_p1": 0.225,
        "mean_predicted_p1": 0.14198577072632984,
        "sample_n": 4137,
        "zero_or_2plus_ci_high": 0.7173709829602,
        "zero_or_2plus_ci_low": 0.689551140660511,
        "zero_or_2plus_hit_rate": 0.7036499879139473
      },
      {
        "actual_p1_rate": 0.28826291079812205,
        "coverage": 0.1365559687139377,
        "max_predicted_p1": 0.2,
        "mean_predicted_p1": 0.12108861081398073,
        "sample_n": 3195,
        "zero_or_2plus_ci_high": 0.7271814872200955,
        "zero_or_2plus_ci_low": 0.6957841449993913,
        "zero_or_2plus_hit_rate": 0.711737089201878
      },
      {
        "actual_p1_rate": 0.28252032520325204,
        "coverage": 0.10514168483138864,
        "max_predicted_p1": 0.175,
        "mean_predicted_p1": 0.10117888584359164,
        "sample_n": 2460,
        "zero_or_2plus_ci_high": 0.7349213558903537,
        "zero_or_2plus_ci_low": 0.6993598338222956,
        "zero_or_2plus_hit_rate": 0.717479674796748
      },
      {
        "actual_p1_rate": 0.26091586794462196,
        "coverage": 0.0802667008590845,
        "max_predicted_p1": 0.15,
        "mean_predicted_p1": 0.08197425392443446,
        "sample_n": 1878,
        "zero_or_2plus_ci_high": 0.7584426366977197,
        "zero_or_2plus_ci_low": 0.718749528344045,
        "zero_or_2plus_hit_rate": 0.739084132055378
      },
      {
        "actual_p1_rate": 0.2512455516014235,
        "coverage": 0.060050433816301235,
        "max_predicted_p1": 0.125,
        "mean_predicted_p1": 0.0632457014972195,
        "sample_n": 1405,
        "zero_or_2plus_ci_high": 0.7707346456326858,
        "zero_or_2plus_ci_low": 0.7254177039617926,
        "zero_or_2plus_hit_rate": 0.7487544483985765
      },
      {
        "actual_p1_rate": 0.22200956937799043,
        "coverage": 0.04466384579219558,
        "max_predicted_p1": 0.1,
        "mean_predicted_p1": 0.04622064081515209,
        "sample_n": 1045,
        "zero_or_2plus_ci_high": 0.8021445002704817,
        "zero_or_2plus_ci_low": 0.7518000402640526,
        "zero_or_2plus_hit_rate": 0.7779904306220096
      }
    ],
    "score_breakdown": {},
    "league_breakdown": {},
    "season_breakdown": {},
    "intersections": {},
    "extreme_calibration": {}
  },
  {
    "experiment_id": "L04_CATBOOST_MULTICLASS_CORE",
    "feature_universe": "CORE",
    "thresholds": [
      {
        "actual_p1_rate": 0.3135736595523165,
        "coverage": 0.6568363465401548,
        "max_predicted_p1": 0.3,
        "mean_predicted_p1": 0.08792294051284408,
        "sample_n": 15368,
        "zero_or_2plus_ci_high": 0.6937140857103802,
        "zero_or_2plus_ci_low": 0.6790454184355593,
        "zero_or_2plus_hit_rate": 0.6864263404476835
      },
      {
        "actual_p1_rate": 0.3136241610738255,
        "coverage": 0.6368337821088174,
        "max_predicted_p1": 0.275,
        "mean_predicted_p1": 0.0816596403990813,
        "sample_n": 14900,
        "zero_or_2plus_ci_high": 0.6937767283736215,
        "zero_or_2plus_ci_low": 0.67887887289171,
        "zero_or_2plus_hit_rate": 0.6863758389261745
      },
      {
        "actual_p1_rate": 0.3133531979765782,
        "coverage": 0.6167884771551908,
        "max_predicted_p1": 0.25,
        "mean_predicted_p1": 0.07577361649890844,
        "sample_n": 14431,
        "zero_or_2plus_ci_high": 0.694164330680245,
        "zero_or_2plus_ci_low": 0.6790299309517847,
        "zero_or_2plus_hit_rate": 0.6866468020234218
      },
      {
        "actual_p1_rate": 0.31355321906956646,
        "coverage": 0.5934948925075865,
        "max_predicted_p1": 0.225,
        "mean_predicted_p1": 0.06941156992929622,
        "sample_n": 13886,
        "zero_or_2plus_ci_high": 0.6941107896017206,
        "zero_or_2plus_ci_low": 0.6786796425534088,
        "zero_or_2plus_hit_rate": 0.6864467809304335
      },
      {
        "actual_p1_rate": 0.3121991576413959,
        "coverage": 0.5682779843569689,
        "max_predicted_p1": 0.2,
        "mean_predicted_p1": 0.06306895635282603,
        "sample_n": 13296,
        "zero_or_2plus_ci_high": 0.6956221757980233,
        "zero_or_2plus_ci_low": 0.6798710220316312,
        "zero_or_2plus_hit_rate": 0.6878008423586041
      },
      {
        "actual_p1_rate": 0.31177262291188346,
        "coverage": 0.5398555370346626,
        "max_predicted_p1": 0.175,
        "mean_predicted_p1": 0.05655446878146591,
        "sample_n": 12631,
        "zero_or_2plus_ci_high": 0.6962473158578059,
        "zero_or_2plus_ci_low": 0.6800929821583926,
        "zero_or_2plus_hit_rate": 0.6882273770881165
      },
      {
        "actual_p1_rate": 0.3117276166456494,
        "coverage": 0.5083985126298244,
        "max_predicted_p1": 0.15,
        "mean_predicted_p1": 0.050023647681516306,
        "sample_n": 11895,
        "zero_or_2plus_ci_high": 0.6965345098969147,
        "zero_or_2plus_ci_low": 0.6798886919333516,
        "zero_or_2plus_hit_rate": 0.6882723833543506
      },
      {
        "actual_p1_rate": 0.31134469010511057,
        "coverage": 0.47168440398341666,
        "max_predicted_p1": 0.125,
        "mean_predicted_p1": 0.0432052170614776,
        "sample_n": 11036,
        "zero_or_2plus_ci_high": 0.6972274200852975,
        "zero_or_2plus_ci_low": 0.6799519094849671,
        "zero_or_2plus_hit_rate": 0.6886553098948894
      },
      {
        "actual_p1_rate": 0.3095645177511817,
        "coverage": 0.42496901312134033,
        "max_predicted_p1": 0.1,
        "mean_predicted_p1": 0.03559843767859268,
        "sample_n": 9943,
        "zero_or_2plus_ci_high": 0.699447604240465,
        "zero_or_2plus_ci_low": 0.6812762683253081,
        "zero_or_2plus_hit_rate": 0.6904354822488182
      }
    ],
    "score_breakdown": {},
    "league_breakdown": {},
    "season_breakdown": {},
    "intersections": {},
    "extreme_calibration": {}
  },
  {
    "experiment_id": "L05_BOOSTING_MULTICLASS_CORE_XG",
    "feature_universe": "CORE_XG",
    "thresholds": [
      {
        "actual_p1_rate": 0.3256364712847839,
        "coverage": 0.6469777062744196,
        "max_predicted_p1": 0.3,
        "mean_predicted_p1": 0.09324543098669227,
        "sample_n": 8445,
        "zero_or_2plus_ci_high": 0.6842768097044533,
        "zero_or_2plus_ci_low": 0.6642916910010942,
        "zero_or_2plus_hit_rate": 0.6743635287152161
      },
      {
        "actual_p1_rate": 0.3260683237418881,
        "coverage": 0.6256799203248296,
        "max_predicted_p1": 0.275,
        "mean_predicted_p1": 0.08666103177381235,
        "sample_n": 8167,
        "zero_or_2plus_ci_high": 0.6840145200036901,
        "zero_or_2plus_ci_low": 0.6636852872093167,
        "zero_or_2plus_hit_rate": 0.6739316762581119
      },
      {
        "actual_p1_rate": 0.32579989842559676,
        "coverage": 0.6033861947445032,
        "max_predicted_p1": 0.25,
        "mean_predicted_p1": 0.08017689395074576,
        "sample_n": 7876,
        "zero_or_2plus_ci_high": 0.6844635936508718,
        "zero_or_2plus_ci_low": 0.6637667628023491,
        "zero_or_2plus_hit_rate": 0.6742001015744032
      },
      {
        "actual_p1_rate": 0.3255321962184318,
        "coverage": 0.5794070328660078,
        "max_predicted_p1": 0.225,
        "mean_predicted_p1": 0.07369162919892241,
        "sample_n": 7563,
        "zero_or_2plus_ci_high": 0.6849372799402763,
        "zero_or_2plus_ci_low": 0.6638211834637252,
        "zero_or_2plus_hit_rate": 0.6744678037815681
      },
      {
        "actual_p1_rate": 0.32446661124965365,
        "coverage": 0.5529763272810848,
        "max_predicted_p1": 0.2,
        "mean_predicted_p1": 0.0670323952223758,
        "sample_n": 7218,
        "zero_or_2plus_ci_high": 0.6862381540084865,
        "zero_or_2plus_ci_low": 0.6646418832297991,
        "zero_or_2plus_hit_rate": 0.6755333887503464
      },
      {
        "actual_p1_rate": 0.3252377468910022,
        "coverage": 0.523634413544779,
        "max_predicted_p1": 0.175,
        "mean_predicted_p1": 0.060318206043492896,
        "sample_n": 6835,
        "zero_or_2plus_ci_high": 0.6857673253267574,
        "zero_or_2plus_ci_low": 0.6635608488069892,
        "zero_or_2plus_hit_rate": 0.6747622531089978
      },
      {
        "actual_p1_rate": 0.32752667922159445,
        "coverage": 0.488163640542404,
        "max_predicted_p1": 0.15,
        "mean_predicted_p1": 0.052882303678376874,
        "sample_n": 6372,
        "zero_or_2plus_ci_high": 0.6838895483750306,
        "zero_or_2plus_ci_low": 0.6608492620543228,
        "zero_or_2plus_hit_rate": 0.6724733207784055
      },
      {
        "actual_p1_rate": 0.3269132435657065,
        "coverage": 0.4494752164253428,
        "max_predicted_p1": 0.125,
        "mean_predicted_p1": 0.04561653951194359,
        "sample_n": 5867,
        "zero_or_2plus_ci_high": 0.6849731635565174,
        "zero_or_2plus_ci_low": 0.6609738381204752,
        "zero_or_2plus_hit_rate": 0.6730867564342935
      },
      {
        "actual_p1_rate": 0.3253697383390216,
        "coverage": 0.4040450471156056,
        "max_predicted_p1": 0.1,
        "mean_predicted_p1": 0.03817152823279613,
        "sample_n": 5274,
        "zero_or_2plus_ci_high": 0.6871436318014861,
        "zero_or_2plus_ci_low": 0.6618626834453507,
        "zero_or_2plus_hit_rate": 0.6746302616609784
      }
    ],
    "score_breakdown": {},
    "league_breakdown": {},
    "season_breakdown": {},
    "intersections": {},
    "extreme_calibration": {}
  },
  {
    "experiment_id": "L06_BOOSTING_MULTICLASS_CORE_SHOTMAP",
    "feature_universe": "CORE_SHOTMAP",
    "thresholds": [
      {
        "actual_p1_rate": 0.33070506227859675,
        "coverage": 0.6707289031961371,
        "max_predicted_p1": 0.3,
        "mean_predicted_p1": 0.11076316696395828,
        "sample_n": 8751,
        "zero_or_2plus_ci_high": 0.6790758550930546,
        "zero_or_2plus_ci_low": 0.6593654535173615,
        "zero_or_2plus_hit_rate": 0.6692949377214032
      },
      {
        "actual_p1_rate": 0.3312268987719089,
        "coverage": 0.6428297692956235,
        "max_predicted_p1": 0.275,
        "mean_predicted_p1": 0.10309227828192646,
        "sample_n": 8387,
        "zero_or_2plus_ci_high": 0.6787665494502556,
        "zero_or_2plus_ci_low": 0.6586251190604121,
        "zero_or_2plus_hit_rate": 0.6687731012280911
      },
      {
        "actual_p1_rate": 0.3318771842236645,
        "coverage": 0.6140875297003142,
        "max_predicted_p1": 0.25,
        "mean_predicted_p1": 0.09562779722404043,
        "sample_n": 8012,
        "zero_or_2plus_ci_high": 0.6783509396619669,
        "zero_or_2plus_ci_low": 0.6577335517591429,
        "zero_or_2plus_hit_rate": 0.6681228157763355
      },
      {
        "actual_p1_rate": 0.3301391441323182,
        "coverage": 0.583889016632176,
        "max_predicted_p1": 0.225,
        "mean_predicted_p1": 0.08831638649212699,
        "sample_n": 7618,
        "zero_or_2plus_ci_high": 0.6803330398543974,
        "zero_or_2plus_ci_low": 0.6592174498773102,
        "zero_or_2plus_hit_rate": 0.6698608558676818
      },
      {
        "actual_p1_rate": 0.32793522267206476,
        "coverage": 0.549015099256534,
        "max_predicted_p1": 0.2,
        "mean_predicted_p1": 0.08046381611221368,
        "sample_n": 7163,
        "zero_or_2plus_ci_high": 0.6828417960747566,
        "zero_or_2plus_ci_low": 0.6611033036129399,
        "zero_or_2plus_hit_rate": 0.6720647773279352
      },
      {
        "actual_p1_rate": 0.32866606443842217,
        "coverage": 0.5090825477121177,
        "max_predicted_p1": 0.175,
        "mean_predicted_p1": 0.0720589672211047,
        "sample_n": 6642,
        "zero_or_2plus_ci_high": 0.6825286078156813,
        "zero_or_2plus_ci_low": 0.6599411929014541,
        "zero_or_2plus_hit_rate": 0.6713339355615778
      },
      {
        "actual_p1_rate": 0.32909721076085163,
        "coverage": 0.4643979458879436,
        "max_predicted_p1": 0.15,
        "mean_predicted_p1": 0.06336710748639489,
        "sample_n": 6059,
        "zero_or_2plus_ci_high": 0.682622742852983,
        "zero_or_2plus_ci_low": 0.6589662652163188,
        "zero_or_2plus_hit_rate": 0.6709027892391484
      },
      {
        "actual_p1_rate": 0.33259749816041206,
        "coverage": 0.4166475051736031,
        "max_predicted_p1": 0.125,
        "mean_predicted_p1": 0.054907379636802034,
        "sample_n": 5436,
        "zero_or_2plus_ci_high": 0.6798049617225141,
        "zero_or_2plus_ci_low": 0.6547636123393039,
        "zero_or_2plus_hit_rate": 0.6674025018395879
      },
      {
        "actual_p1_rate": 0.33511092150170646,
        "coverage": 0.3593163179274929,
        "max_predicted_p1": 0.1,
        "mean_predicted_p1": 0.0457360988086522,
        "sample_n": 4688,
        "zero_or_2plus_ci_high": 0.6782613273532936,
        "zero_or_2plus_ci_low": 0.6512468228194939,
        "zero_or_2plus_hit_rate": 0.6648890784982935
      }
    ],
    "score_breakdown": {},
    "league_breakdown": {},
    "season_breakdown": {},
    "intersections": {},
    "extreme_calibration": {}
  },
  {
    "experiment_id": "L07_EXTRATREES_MULTICLASS_CORE",
    "feature_universe": "CORE",
    "thresholds": [
      {
        "actual_p1_rate": 0.3135736595523165,
        "coverage": 0.6568363465401548,
        "max_predicted_p1": 0.3,
        "mean_predicted_p1": 0.08792294051284408,
        "sample_n": 15368,
        "zero_or_2plus_ci_high": 0.6937140857103802,
        "zero_or_2plus_ci_low": 0.6790454184355593,
        "zero_or_2plus_hit_rate": 0.6864263404476835
      },
      {
        "actual_p1_rate": 0.3136241610738255,
        "coverage": 0.6368337821088174,
        "max_predicted_p1": 0.275,
        "mean_predicted_p1": 0.0816596403990813,
        "sample_n": 14900,
        "zero_or_2plus_ci_high": 0.6937767283736215,
        "zero_or_2plus_ci_low": 0.67887887289171,
        "zero_or_2plus_hit_rate": 0.6863758389261745
      },
      {
        "actual_p1_rate": 0.3133531979765782,
        "coverage": 0.6167884771551908,
        "max_predicted_p1": 0.25,
        "mean_predicted_p1": 0.07577361649890844,
        "sample_n": 14431,
        "zero_or_2plus_ci_high": 0.694164330680245,
        "zero_or_2plus_ci_low": 0.6790299309517847,
        "zero_or_2plus_hit_rate": 0.6866468020234218
      },
      {
        "actual_p1_rate": 0.31355321906956646,
        "coverage": 0.5934948925075865,
        "max_predicted_p1": 0.225,
        "mean_predicted_p1": 0.06941156992929622,
        "sample_n": 13886,
        "zero_or_2plus_ci_high": 0.6941107896017206,
        "zero_or_2plus_ci_low": 0.6786796425534088,
        "zero_or_2plus_hit_rate": 0.6864467809304335
      },
      {
        "actual_p1_rate": 0.3121991576413959,
        "coverage": 0.5682779843569689,
        "max_predicted_p1": 0.2,
        "mean_predicted_p1": 0.06306895635282603,
        "sample_n": 13296,
        "zero_or_2plus_ci_high": 0.6956221757980233,
        "zero_or_2plus_ci_low": 0.6798710220316312,
        "zero_or_2plus_hit_rate": 0.6878008423586041
      },
      {
        "actual_p1_rate": 0.31177262291188346,
        "coverage": 0.5398555370346626,
        "max_predicted_p1": 0.175,
        "mean_predicted_p1": 0.05655446878146591,
        "sample_n": 12631,
        "zero_or_2plus_ci_high": 0.6962473158578059,
        "zero_or_2plus_ci_low": 0.6800929821583926,
        "zero_or_2plus_hit_rate": 0.6882273770881165
      },
      {
        "actual_p1_rate": 0.3117276166456494,
        "coverage": 0.5083985126298244,
        "max_predicted_p1": 0.15,
        "mean_predicted_p1": 0.050023647681516306,
        "sample_n": 11895,
        "zero_or_2plus_ci_high": 0.6965345098969147,
        "zero_or_2plus_ci_low": 0.6798886919333516,
        "zero_or_2plus_hit_rate": 0.6882723833543506
      },
      {
        "actual_p1_rate": 0.31134469010511057,
        "coverage": 0.47168440398341666,
        "max_predicted_p1": 0.125,
        "mean_predicted_p1": 0.0432052170614776,
        "sample_n": 11036,
        "zero_or_2plus_ci_high": 0.6972274200852975,
        "zero_or_2plus_ci_low": 0.6799519094849671,
        "zero_or_2plus_hit_rate": 0.6886553098948894
      },
      {
        "actual_p1_rate": 0.3095645177511817,
        "coverage": 0.42496901312134033,
        "max_predicted_p1": 0.1,
        "mean_predicted_p1": 0.03559843767859268,
        "sample_n": 9943,
        "zero_or_2plus_ci_high": 0.699447604240465,
        "zero_or_2plus_ci_low": 0.6812762683253081,
        "zero_or_2plus_hit_rate": 0.6904354822488182
      }
    ],
    "score_breakdown": {},
    "league_breakdown": {},
    "season_breakdown": {},
    "intersections": {},
    "extreme_calibration": {}
  },
  {
    "experiment_id": "L08_BOOSTING_BINARY_P1_CORE",
    "feature_universe": "CORE",
    "thresholds": [
      {
        "actual_p1_rate": 0.3189173286260665,
        "coverage": 0.8716502115655853,
        "max_predicted_p1": 0.3,
        "mean_predicted_p1": 0.08323362795916597,
        "sample_n": 20394,
        "zero_or_2plus_ci_high": 0.6874444608297618,
        "zero_or_2plus_ci_low": 0.6746526765026812,
        "zero_or_2plus_hit_rate": 0.6810826713739335
      },
      {
        "actual_p1_rate": 0.31917560926687394,
        "coverage": 0.852331495490875,
        "max_predicted_p1": 0.275,
        "mean_predicted_p1": 0.07861027959984143,
        "sample_n": 19942,
        "zero_or_2plus_ci_high": 0.6872589142972937,
        "zero_or_2plus_ci_low": 0.6743202156125462,
        "zero_or_2plus_hit_rate": 0.6808243907331261
      },
      {
        "actual_p1_rate": 0.3190209286779452,
        "coverage": 0.8311749369577296,
        "max_predicted_p1": 0.25,
        "mean_predicted_p1": 0.0739526279614076,
        "sample_n": 19447,
        "zero_or_2plus_ci_high": 0.6874936383542657,
        "zero_or_2plus_ci_low": 0.6743930190894362,
        "zero_or_2plus_hit_rate": 0.6809790713220548
      },
      {
        "actual_p1_rate": 0.3188198527464378,
        "coverage": 0.806898320297474,
        "max_predicted_p1": 0.225,
        "mean_predicted_p1": 0.06905033970410764,
        "sample_n": 18879,
        "zero_or_2plus_ci_high": 0.6877902746049007,
        "zero_or_2plus_ci_low": 0.6744963025989967,
        "zero_or_2plus_hit_rate": 0.6811801472535621
      },
      {
        "actual_p1_rate": 0.3178103230760792,
        "coverage": 0.7792024618540838,
        "max_predicted_p1": 0.2,
        "mean_predicted_p1": 0.06397681800590005,
        "sample_n": 18231,
        "zero_or_2plus_ci_high": 0.6889096456366604,
        "zero_or_2plus_ci_low": 0.6753929459159849,
        "zero_or_2plus_hit_rate": 0.6821896769239207
      },
      {
        "actual_p1_rate": 0.31691796135985784,
        "coverage": 0.7455229302902081,
        "max_predicted_p1": 0.175,
        "mean_predicted_p1": 0.05840803641133748,
        "sample_n": 17443,
        "zero_or_2plus_ci_high": 0.6899458254809905,
        "zero_or_2plus_ci_low": 0.6761376295142911,
        "zero_or_2plus_hit_rate": 0.6830820386401422
      },
      {
        "actual_p1_rate": 0.316836487142164,
        "coverage": 0.704705731504039,
        "max_predicted_p1": 0.15,
        "mean_predicted_p1": 0.052400179731817674,
        "sample_n": 16488,
        "zero_or_2plus_ci_high": 0.6902215503344088,
        "zero_or_2plus_ci_low": 0.6760201465119732,
        "zero_or_2plus_hit_rate": 0.683163512857836
      },
      {
        "actual_p1_rate": 0.3175141980547033,
        "coverage": 0.6547420609479848,
        "max_predicted_p1": 0.125,
        "mean_predicted_p1": 0.04592884365025728,
        "sample_n": 15319,
        "zero_or_2plus_ci_high": 0.6898108620098986,
        "zero_or_2plus_ci_low": 0.6750692429644634,
        "zero_or_2plus_hit_rate": 0.6824858019452967
      },
      {
        "actual_p1_rate": 0.3167571480275063,
        "coverage": 0.5904603154250545,
        "max_predicted_p1": 0.1,
        "mean_predicted_p1": 0.038734823679443045,
        "sample_n": 13815,
        "zero_or_2plus_ci_high": 0.6909485307618888,
        "zero_or_2plus_ci_low": 0.6754352949073927,
        "zero_or_2plus_hit_rate": 0.6832428519724937
      }
    ],
    "score_breakdown": {},
    "league_breakdown": {},
    "season_breakdown": {},
    "intersections": {},
    "extreme_calibration": {}
  },
  {
    "experiment_id": "L09_POISSON_COUNT_CORE",
    "feature_universe": "CORE",
    "thresholds": [
      {
        "actual_p1_rate": null,
        "coverage": 0.0,
        "max_predicted_p1": 0.3,
        "mean_predicted_p1": null,
        "sample_n": 0,
        "zero_or_2plus_ci_high": null,
        "zero_or_2plus_ci_low": null,
        "zero_or_2plus_hit_rate": null
      },
      {
        "actual_p1_rate": null,
        "coverage": 0.0,
        "max_predicted_p1": 0.275,
        "mean_predicted_p1": null,
        "sample_n": 0,
        "zero_or_2plus_ci_high": null,
        "zero_or_2plus_ci_low": null,
        "zero_or_2plus_hit_rate": null
      },
      {
        "actual_p1_rate": null,
        "coverage": 0.0,
        "max_predicted_p1": 0.25,
        "mean_predicted_p1": null,
        "sample_n": 0,
        "zero_or_2plus_ci_high": null,
        "zero_or_2plus_ci_low": null,
        "zero_or_2plus_hit_rate": null
      },
      {
        "actual_p1_rate": null,
        "coverage": 0.0,
        "max_predicted_p1": 0.225,
        "mean_predicted_p1": null,
        "sample_n": 0,
        "zero_or_2plus_ci_high": null,
        "zero_or_2plus_ci_low": null,
        "zero_or_2plus_hit_rate": null
      },
      {
        "actual_p1_rate": null,
        "coverage": 0.0,
        "max_predicted_p1": 0.2,
        "mean_predicted_p1": null,
        "sample_n": 0,
        "zero_or_2plus_ci_high": null,
        "zero_or_2plus_ci_low": null,
        "zero_or_2plus_hit_rate": null
      },
      {
        "actual_p1_rate": null,
        "coverage": 0.0,
        "max_predicted_p1": 0.175,
        "mean_predicted_p1": null,
        "sample_n": 0,
        "zero_or_2plus_ci_high": null,
        "zero_or_2plus_ci_low": null,
        "zero_or_2plus_hit_rate": null
      },
      {
        "actual_p1_rate": null,
        "coverage": 0.0,
        "max_predicted_p1": 0.15,
        "mean_predicted_p1": null,
        "sample_n": 0,
        "zero_or_2plus_ci_high": null,
        "zero_or_2plus_ci_low": null,
        "zero_or_2plus_hit_rate": null
      },
      {
        "actual_p1_rate": null,
        "coverage": 0.0,
        "max_predicted_p1": 0.125,
        "mean_predicted_p1": null,
        "sample_n": 0,
        "zero_or_2plus_ci_high": null,
        "zero_or_2plus_ci_low": null,
        "zero_or_2plus_hit_rate": null
      },
      {
        "actual_p1_rate": null,
        "coverage": 0.0,
        "max_predicted_p1": 0.1,
        "mean_predicted_p1": null,
        "sample_n": 0,
        "zero_or_2plus_ci_high": null,
        "zero_or_2plus_ci_low": null,
        "zero_or_2plus_hit_rate": null
      }
    ],
    "score_breakdown": {},
    "league_breakdown": {},
    "season_breakdown": {},
    "intersections": {},
    "extreme_calibration": {}
  },
  {
    "experiment_id": "L10_ENSEMBLE_P1_OOF",
    "feature_universe": "CORE",
    "thresholds": [
      {
        "actual_p1_rate": 0.10638297872340426,
        "coverage": 0.008035218190366285,
        "max_predicted_p1": 0.3,
        "mean_predicted_p1": 0.2880261728914698,
        "sample_n": 188,
        "zero_or_2plus_ci_high": 0.9300717400025025,
        "zero_or_2plus_ci_low": 0.8413986236498567,
        "zero_or_2plus_hit_rate": 0.8936170212765957
      },
      {
        "actual_p1_rate": 0.0,
        "coverage": 0.001367696713253836,
        "max_predicted_p1": 0.275,
        "mean_predicted_p1": 0.2654300504535724,
        "sample_n": 32,
        "zero_or_2plus_ci_high": 1.0,
        "zero_or_2plus_ci_low": 0.8928208017449293,
        "zero_or_2plus_hit_rate": 1.0
      },
      {
        "actual_p1_rate": 0.0,
        "coverage": 0.00012822156686754713,
        "max_predicted_p1": 0.25,
        "mean_predicted_p1": 0.248583034029583,
        "sample_n": 3,
        "zero_or_2plus_ci_high": 1.0,
        "zero_or_2plus_ci_low": 0.4385029682449546,
        "zero_or_2plus_hit_rate": 1.0
      },
      {
        "actual_p1_rate": null,
        "coverage": 0.0,
        "max_predicted_p1": 0.225,
        "mean_predicted_p1": null,
        "sample_n": 0,
        "zero_or_2plus_ci_high": null,
        "zero_or_2plus_ci_low": null,
        "zero_or_2plus_hit_rate": null
      },
      {
        "actual_p1_rate": null,
        "coverage": 0.0,
        "max_predicted_p1": 0.2,
        "mean_predicted_p1": null,
        "sample_n": 0,
        "zero_or_2plus_ci_high": null,
        "zero_or_2plus_ci_low": null,
        "zero_or_2plus_hit_rate": null
      },
      {
        "actual_p1_rate": null,
        "coverage": 0.0,
        "max_predicted_p1": 0.175,
        "mean_predicted_p1": null,
        "sample_n": 0,
        "zero_or_2plus_ci_high": null,
        "zero_or_2plus_ci_low": null,
        "zero_or_2plus_hit_rate": null
      },
      {
        "actual_p1_rate": null,
        "coverage": 0.0,
        "max_predicted_p1": 0.15,
        "mean_predicted_p1": null,
        "sample_n": 0,
        "zero_or_2plus_ci_high": null,
        "zero_or_2plus_ci_low": null,
        "zero_or_2plus_hit_rate": null
      },
      {
        "actual_p1_rate": null,
        "coverage": 0.0,
        "max_predicted_p1": 0.125,
        "mean_predicted_p1": null,
        "sample_n": 0,
        "zero_or_2plus_ci_high": null,
        "zero_or_2plus_ci_low": null,
        "zero_or_2plus_hit_rate": null
      },
      {
        "actual_p1_rate": null,
        "coverage": 0.0,
        "max_predicted_p1": 0.1,
        "mean_predicted_p1": null,
        "sample_n": 0,
        "zero_or_2plus_ci_high": null,
        "zero_or_2plus_ci_low": null,
        "zero_or_2plus_hit_rate": null
      }
    ],
    "score_breakdown": {},
    "league_breakdown": {},
    "season_breakdown": {},
    "intersections": {},
    "extreme_calibration": {}
  }
]
```

## CORE / SCORE_ONLY / enhanced intersections

```json
{
  "L01_LOGISTIC_MULTICLASS_SCORE_ONLY": {},
  "L02_LOGISTIC_MULTICLASS_CORE": {},
  "L03_HISTGRADIENTBOOSTING_MULTICLASS_CORE": {},
  "L04_CATBOOST_MULTICLASS_CORE": {},
  "L05_BOOSTING_MULTICLASS_CORE_XG": {},
  "L06_BOOSTING_MULTICLASS_CORE_SHOTMAP": {},
  "L07_EXTRATREES_MULTICLASS_CORE": {},
  "L08_BOOSTING_BINARY_P1_CORE": {},
  "L09_POISSON_COUNT_CORE": {},
  "L10_ENSEMBLE_P1_OOF": {}
}
```

## Performance evidence

```json
{
  "source": "runtime_canary_or_benchmark_not_run_by_local_report",
  "collector_health": "PENDING",
  "daily_index_cache": {
    "daily_index_network_requests": "NOT_OBSERVED",
    "daily_index_cache_hits": "NOT_OBSERVED",
    "daily_index_cache_misses": "NOT_OBSERVED",
    "daily_index_singleflight_waiters": "NOT_OBSERVED",
    "daily_index_age_seconds": "NOT_OBSERVED"
  },
  "resolver": {
    "resolver_attempts": "NOT_OBSERVED",
    "resolver_candidate_scans": "NOT_OBSERVED",
    "resolver_negative_cache_hits": "NOT_OBSERVED",
    "confirmed_link_fast_path": "NOT_OBSERVED"
  },
  "resources": {
    "available_ram_bytes": 4170997760,
    "total_ram_bytes": 16477036544,
    "swap_used_bytes": 1839611904,
    "swap_percent": 9.0,
    "free_disk_bytes": 130633015296,
    "total_disk_bytes": 510869368832
  },
  "database": {
    "strategy_evaluations_bind_mismatch_errors": "NOT_OBSERVED",
    "db_transactions": "NOT_OBSERVED",
    "db_commits": "NOT_OBSERVED",
    "db_rollbacks": "NOT_OBSERVED",
    "wal": "NOT_OBSERVED"
  },
  "slow_operations": "NOT_OBSERVED"
}
```

## Scope guard

- Target: `regulation_ft_goals - halftime_goals`; extra-time-ambiguous matches remain excluded.
- Locked/test data is reserved and is not used for model selection.
- No historical Tipico HT odds are available in this research dataset; therefore there is no ROI or betting recommendation.
- CT110 deployment/runtime canaries must be executed on CT110 and are not inferred from this local report.
