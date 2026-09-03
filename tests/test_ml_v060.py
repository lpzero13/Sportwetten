"""Focused tests for the V0.6 research factory.

These tests intentionally use a tiny in-memory-shaped Parquet cache.  They
exercise the contracts that must remain stable without training the local
10-model run as part of the normal test suite.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from research.ml_v060 import (
    DATASET_SCHEMA_VERSION,
    FEATURES_BY_UNIVERSE,
    LeakageError,
    DatasetBuilder,
    ExperimentPlanner,
    ExperimentRegistry,
    ExperimentRunner,
    _FittedModel,
    _HistoryState,
    _add_pair,
    _evaluate_predictions,
    _extra_time_from_events,
    _feature_requirements,
    _shotmap_features,
    _split_rows,
    classify_h2_goal_target,
    experiment_config_hash,
    feature_catalog,
    load_search_space,
)


def test_h2_target_is_regulation_only_and_maps_loss_middle() -> None:
    one = classify_h2_goal_target(1, 0, 1, 1)
    assert one["target_h2_goal_class"] == "H2_GOALS_1"
    assert one["middle_loss"] is True
    assert one["h2_total_goals"] == 1
    assert classify_h2_goal_target(0, 0, 2, 0)["target_h2_goal_class"] == "H2_GOALS_2_PLUS"
    assert classify_h2_goal_target(2, 1, 2, 1)["target_h2_goal_class"] == "H2_GOALS_0"
    assert classify_h2_goal_target(1, 1, 2, 2)["target_h2_goal_class"] == "H2_GOALS_2_PLUS"

    extra_time = classify_h2_goal_target(0, 0, 1, 1, extra_time_ambiguous=True)
    assert extra_time["target_quality"] == "INVALID_EXTRA_TIME_AMBIGUOUS"
    assert extra_time["ml_eligible"] is False
    assert classify_h2_goal_target(2, 0, 1, 0)["ml_eligible"] is False


def test_extra_time_markers_are_excluded_from_the_regulation_target() -> None:
    assert _extra_time_from_events([{"period": "FirstHalfExtra", "minute": 95}]) is True
    assert _extra_time_from_events([{"extra_json": '{"isPenaltyShootoutEvent": true}', "minute": 90}]) is True


def test_missing_pairs_do_not_turn_into_fake_zero_values() -> None:
    values: dict[str, object] = {}
    _add_pair(values, "metric", None, 2)
    assert values == {
        "metric_home": None,
        "metric_away": 2.0,
        "metric_total": None,
        "metric_diff": None,
        "metric_home_share": None,
        "metric_home_away_ratio": None,
    }


def test_feature_catalog_has_no_halftime_leakage() -> None:
    catalog = feature_catalog()
    assert catalog
    assert all(item["availability_cutoff"] in {"HALFTIME", "FIRST_HALF", "ROLLING_HISTORY"} for item in catalog)
    assert not [item for item in catalog if "ft_" in item["feature_name"] or "final" in item["feature_name"].lower()]


def _cache_row(row_id: str, kickoff: datetime, target_index: int) -> dict[str, object]:
    ht_home, ht_away = (target_index, 0)
    h2_home, h2_away = (0, 0) if target_index == 0 else (1, 0) if target_index == 1 else (2, 0)
    row: dict[str, object] = {
        "row_id": row_id,
        "fotmob_match_id": row_id,
        "kickoff": kickoff.isoformat(),
        "country": "Testland",
        "country_code": "TST",
        "league_id": "1",
        "league_name": "Test League",
        "season": "2025/26",
        "season_id": "2025-26",
        "home_team": f"Home {row_id}",
        "away_team": f"Away {row_id}",
        "home_team_id": f"h-{row_id}",
        "away_team_id": f"a-{row_id}",
        "ht_home": ht_home,
        "ht_away": ht_away,
        "ht_total": ht_home + ht_away,
        "ht_diff": ht_home - ht_away,
        "regulation_ft_home": ht_home + h2_home,
        "regulation_ft_away": ht_away + h2_away,
        "h2_home_goals": h2_home,
        "h2_away_goals": h2_away,
        "h2_total_goals": h2_home + h2_away,
        "target_h2_goal_class": ("H2_GOALS_0", "H2_GOALS_1", "H2_GOALS_2_PLUS")[target_index],
        "middle_loss": target_index == 1,
        "target_quality": "VALID_REGULATION",
        "ml_eligible": True,
        "extra_time_ambiguous": False,
        "xg_available": False,
        "shotmap_available": False,
        "events_available": False,
        "feature_coverage": 1.0,
    }
    for feature in FEATURES_BY_UNIVERSE["SCORE_ONLY"]:
        row[feature] = float(target_index) if feature == "ht_total_goals" else 0.0
    return row


def _write_cache(builder: DatasetBuilder, rows: list[dict[str, object]]) -> dict[str, object]:
    pa = pytest.importorskip("pyarrow")
    pq = pytest.importorskip("pyarrow.parquet")
    builder.cache_dir.mkdir(parents=True, exist_ok=True)
    schema = builder._arrow_schema()
    table = pa.Table.from_pylist([{field.name: row.get(field.name) for field in schema} for row in rows], schema=schema)
    pq.write_table(table, builder.dataset_path, compression="zstd")
    manifest: dict[str, object] = {
        "dataset_schema_version": DATASET_SCHEMA_VERSION,
        "feature_schema_version": "v060_feature_schema_v1",
        "target_schema_version": "v060_h2_target_v1",
        "model_cutoff": "HALFTIME",
        "target_definition": "regulation_ft_goals - halftime_goals",
        "dataset_path": str(builder.dataset_path),
        "dataset_hash": __import__("research.ml_v060", fromlist=["_hash_file"])._hash_file(builder.dataset_path),
        "match_count": len(rows),
        "eligible_match_count": len(rows),
        "target_distribution": {
            target: sum(row["target_h2_goal_class"] == target for row in rows)
            for target in ("H2_GOALS_0", "H2_GOALS_1", "H2_GOALS_2_PLUS")
        },
        "feature_coverage": {feature: 1.0 for feature in FEATURES_BY_UNIVERSE["SCORE_ONLY"]},
        "feature_columns_by_universe": FEATURES_BY_UNIVERSE,
        "feature_catalog": feature_catalog(),
        "source_date_range": {"from": rows[0]["kickoff"][:10], "to": rows[-1]["kickoff"][:10]},
    }
    builder.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return manifest


def test_dataset_audit_recomputes_targets_and_rejects_forbidden_catalog(tmp_path: Path) -> None:
    builder = DatasetBuilder(tmp_path)
    rows = [_cache_row("1", datetime(2025, 1, 1, tzinfo=timezone.utc), 0)]
    manifest = _write_cache(builder, rows)
    audited = builder.audit()
    assert audited["status"] == "PASS"
    assert audited["checked_target_rows"] == 1

    bad = dict(manifest)
    bad["feature_catalog"] = [{"feature_name": "final_score", "feature_origin": "FULL_TIME", "availability_cutoff": "HALFTIME"}]
    builder.manifest_path.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(LeakageError):
        builder.audit()


def test_planner_generates_ten_local_and_one_hundred_unique_standard_configs(tmp_path: Path) -> None:
    search_space = load_search_space()
    local_registry = ExperimentRegistry(tmp_path / "local.sqlite")
    try:
        local = ExperimentPlanner(local_registry, search_space).plan_new(10, mode="local")
        assert len(local) == 10
        assert len({row["config_hash"] for row in local}) == 10
    finally:
        local_registry.close()

    standard_registry = ExperimentRegistry(tmp_path / "standard.sqlite")
    try:
        planned = ExperimentPlanner(standard_registry, search_space).plan_new(100, mode="standard")
        assert len(planned) == 100
        assert len({row["config_hash"] for row in planned}) == 100
        assert len({row["feature_set"] for row in planned}) >= 4
        assert len({row["model_family"] for row in planned}) >= 4
    finally:
        standard_registry.close()


def test_registry_deduplicates_and_recovers_running_experiments(tmp_path: Path) -> None:
    registry = ExperimentRegistry(tmp_path / "registry.sqlite")
    try:
        config = {
            "model_family": "LOGISTIC",
            "target_type": "MULTICLASS",
            "feature_universe": "SCORE_ONLY",
            "training_window": "ALL",
            "time_decay": "NONE",
            "calibration": "NONE",
            "league_scope": "GLOBAL",
            "hyperparameters": {"C": 1.0},
        }
        first = registry.insert_planned(config, "dataset", "commit")
        second = registry.insert_planned(config, "dataset", "commit")
        assert first["experiment_id"] == second["experiment_id"]
        assert len(registry.list()) == 1
        registry.update(first["experiment_id"], status="RUNNING")
        assert registry.recover_running() == 1
        assert registry.get(first["experiment_id"])["status"] == "INTERRUPTED"
    finally:
        registry.close()


def test_runner_uses_walk_forward_and_keeps_locked_period_out_of_metrics(tmp_path: Path) -> None:
    base = datetime(2025, 1, 1, tzinfo=timezone.utc)
    rows = [_cache_row(str(index), base + timedelta(days=index), index % 3) for index in range(36)]
    builder = DatasetBuilder(tmp_path)
    manifest = _write_cache(builder, rows)
    registry = ExperimentRegistry(tmp_path / "registry.sqlite")
    try:
        config = {
            "model_family": "LOGISTIC",
            "target_type": "MULTICLASS",
            "feature_universe": "SCORE_ONLY",
            "training_window": "ALL",
            "time_decay": "NONE",
            "calibration": "NONE",
            "league_scope": "GLOBAL",
            "hyperparameters": {"C": 1.0, "max_iter": 200},
        }
        record = {"experiment_id": "SYNTHETIC", "config": config}
        result = ExperimentRunner(tmp_path, manifest, rows, registry).run(record)
        metrics = result["metrics"]
        assert metrics["locked_test_evaluated"] is False
        assert metrics["locked_test_n"] == 0
        assert metrics["locked_test_available_n"] > 0
        assert metrics["validation_n"] > 0
        assert len(metrics["folds"]) >= 1
        assert Path(result["artifact_path"]).exists()
    finally:
        registry.close()


def test_split_reserves_newest_period() -> None:
    base = datetime(2025, 1, 1, tzinfo=timezone.utc)
    rows = [{"row_id": str(i), "kickoff": (base + timedelta(days=i)).isoformat()} for i in range(20)]
    dev, locked, folds = _split_rows(rows)
    assert locked
    assert max(int(row["row_id"]) for row in dev) < min(int(row["row_id"]) for row in locked)
    assert folds


def test_history_features_are_prior_match_only() -> None:
    history = _HistoryState()
    first = {
        "row_id": "first",
        "home_team_id": "team",
        "away_team_id": "other",
        "season": "2025/26",
        "country_code": "TST",
        "league_id": "1",
        "ml_eligible": True,
        "h2_home_goals": 2,
        "h2_away_goals": 0,
        "h2_total_goals": 2,
        "target_h2_goal_class": "H2_GOALS_2_PLUS",
        "ht_shots_home": 4,
        "ht_shots_away": 2,
        "ht_xg_home": None,
        "ht_xg_away": None,
        "kickoff": "2025-01-01T12:00:00+00:00",
    }
    before = history.features_for(first)
    assert before["history_last5_h2_scored_home"] is None
    history.update(first)
    after_row = dict(first)
    after_row["kickoff"] = "2025-01-02T12:00:00+00:00"
    after = history.features_for(after_row)
    assert after["history_last5_h2_scored_home"] == 2.0


def test_missing_xg_excludes_only_xg_universe(tmp_path: Path) -> None:
    builder = DatasetBuilder(tmp_path)
    row = _cache_row("1", datetime(2025, 1, 1, tzinfo=timezone.utc), 0)
    manifest = _write_cache(builder, [row])
    registry = ExperimentRegistry(tmp_path / "registry.sqlite")
    try:
        runner = ExperimentRunner(tmp_path, manifest, [row], registry)
        assert _feature_requirements("CORE") == ()
        assert runner._eligible_rows({"feature_universe": "CORE"})
        assert runner._eligible_rows({"feature_universe": "CORE_XG"}) == []
    finally:
        registry.close()


def test_first_half_momentum_features_use_last15_vs_previous15() -> None:
    shots = []
    for minute, side, count in ((20, True, 2), (40, True, 3), (20, False, 1), (40, False, 2)):
        for _ in range(count):
            shots.append(
                {
                    "period": "FIRST_HALF",
                    "minute": minute,
                    "is_home": side,
                    "xg": 0.1,
                    "xgot": None,
                    "outcome": "missed",
                    "shot_type": "",
                    "situation": "open play",
                    "is_inside_box": False,
                    "is_on_target": False,
                }
            )
    features, available = _shotmap_features(shots, {})
    assert available is True
    assert features["momentum_last15_minus_previous15_shots_home"] == 1.0
    assert features["momentum_last15_minus_previous15_shots_away"] == 1.0
    assert features["momentum_last15_shots_home_share_delta"] == pytest.approx(0.6 - (2 / 3))


def test_prediction_metrics_include_zero_or_two_plus_thresholds() -> None:
    predictions = [
        {"row_id": str(i), "actual_index": i % 3, "p0": 0.6, "p1": 0.2, "p2": 0.2, "fold": 1}
        for i in range(12)
    ]
    metrics = _evaluate_predictions(predictions, denominator=20)
    assert metrics["coverage"] == pytest.approx(0.6)
    assert len(metrics["p1_thresholds"]) == 9
    assert all("zero_or_2plus_ci_low" in row for row in metrics["p1_thresholds"])


def test_model_probability_outputs_sum_to_one() -> None:
    np = pytest.importorskip("numpy")

    class RawModel:
        classes_ = np.asarray([0, 1, 2])

        @staticmethod
        def predict_proba(values):
            return np.asarray([[2.0, 3.0, 5.0] for _ in range(len(values))])

    fitted = _FittedModel(RawModel(), "MULTICLASS", "NONE")
    probabilities = fitted.predict(np.zeros((2, 1)))["probabilities"]
    assert np.allclose(probabilities.sum(axis=1), 1.0)
