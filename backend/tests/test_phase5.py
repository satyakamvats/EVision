import json
from datetime import datetime, timezone
from pathlib import Path

from app.ml_features import FEATURE_NAMES, build_examples
from app.replay import ScenarioReplayStore


def test_phase5_features_are_canonical_and_chronological():
    result = build_examples("artifacts/datasets/karnataka_scenario", max_rows=5000, seed=42)
    assert result["feature_names"] == FEATURE_NAMES
    split = result["split_metadata"]
    assert split["method"] == "chronological_as_of_timestamp"
    for horizon in (1, 2, 3):
        parts = result["by_horizon"][horizon]
        assert parts["train"] and parts["validation"] and parts["test"]
        assert all(item[1] >= 168 for part in parts.values() for item in part[:10])


def test_registered_phase5_artifacts_have_all_quantiles():
    root = Path("artifacts/models/scenario_estimate")
    registry = json.loads((root / "registry.json").read_text(encoding="utf-8"))
    assert registry["data_source"] == "scenario_estimate"
    for horizon in (1, 2, 3):
        for quantile in ("q10", "q50", "q90"):
            assert (root / f"horizon-{horizon}-{quantile}.json").exists()


def test_live_clock_uses_same_weekday_hour_reference_instead_of_first_row():
    rows = [
        {"_dt": datetime(2026, 1, 5, 12, tzinfo=timezone.utc), "occupancy_rate": .1},
        {"_dt": datetime(2026, 1, 12, 12, tzinfo=timezone.utc), "occupancy_rate": .8},
    ]
    selected = ScenarioReplayStore._reference_row(rows, datetime(2026, 10, 26, 12, tzinfo=timezone.utc))
    assert selected["occupancy_rate"] == .8
