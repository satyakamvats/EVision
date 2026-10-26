"""Train and evaluate production XGBoost forecasting artifacts."""
from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import platform
from typing import Any

import numpy as np

from .forecasting import apply_cqr, fit_cqr, metrics, pinball_loss
from .ml_features import FEATURE_NAMES, build_examples


def _arrays(rows: list[tuple]) -> tuple[np.ndarray, np.ndarray]:
    return np.asarray([row[3] for row in rows], dtype=np.float32), np.asarray([row[4] for row in rows], dtype=np.float32)


def _fit_xgb(x: np.ndarray, y: np.ndarray, seed: int, quantile: float | None = None):
    import xgboost as xgb
    params = {"max_depth": 6, "eta": .05, "subsample": .9, "colsample_bytree": .9, "seed": seed, "nthread": 2, "tree_method": "hist", "objective": "reg:squarederror"}
    if quantile is not None:
        params["objective"] = "reg:quantileerror"; params["quantile_alpha"] = quantile
    return xgb.train(params, xgb.DMatrix(x, label=y), num_boost_round=140)


def _predict(model, x: np.ndarray) -> np.ndarray:
    import xgboost as xgb
    return np.asarray(model.predict(xgb.DMatrix(x)), dtype=float)


def _baseline_history(train_rows: list[tuple], rows: list[tuple], observations: dict[str, list[dict[str, Any]]]) -> np.ndarray:
    values: dict[tuple[str, int, int], list[float]] = {}
    for station_id, index, _, _, target in train_rows:
        dt = observations[station_id][index]["_dt"]
        values.setdefault((station_id, dt.weekday(), dt.hour), []).append(target)
    return np.asarray([float(np.mean(values.get((sid, observations[sid][idx]["_dt"].weekday(), observations[sid][idx]["_dt"].hour), [observations[sid][idx]["occupancy_rate"]]))) for sid, idx, _, _, _ in rows])


def _reliability(actual: np.ndarray, lower: np.ndarray, upper: np.ndarray) -> list[dict[str, float]]:
    bins = []
    for lower_bound in np.linspace(0, 1, 6)[:-1]:
        upper_bound = lower_bound + .2
        mask = (actual >= lower_bound) & (actual < upper_bound)
        if np.any(mask): bins.append({"bin": round(float(lower_bound), 2), "coverage": float(np.mean((actual[mask] >= lower[mask]) & (actual[mask] <= upper[mask]))), "count": int(np.sum(mask))})
    return bins


def _grouped_slice_metrics(actual: np.ndarray, predicted: np.ndarray, groups: dict[str, list[int]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for label, indices in sorted(groups.items()):
        if not indices:
            continue
        mask = np.asarray(indices, dtype=int)
        score = asdict(metrics(actual[mask], predicted[mask]))
        score["count"] = int(len(mask))
        result[label] = score
    return result


def train_production_models(root: str | Path = "artifacts", scenario_root: str | Path = "artifacts/datasets/karnataka_scenario", seed: int = 42, max_rows: int = 180000) -> dict[str, Any]:
    examples = build_examples(scenario_root, max_rows=max_rows, seed=seed)
    output_root = Path(root); model_root = output_root / "models" / "scenario_estimate"; model_root.mkdir(parents=True, exist_ok=True)
    dataset_hash = hashlib.sha256(json.dumps({"source": "karnataka_scenario", "split": examples["split_metadata"], "features": FEATURE_NAMES}, sort_keys=True).encode()).hexdigest()
    report: dict[str, Any] = {"data_source": "scenario_estimate", "dataset": "karnataka_scenario", "dataset_hash": dataset_hash, "split": examples["split_metadata"], "seed": seed, "models": {}, "python": platform.python_version()}
    for horizon in (1, 2, 3):
        parts = examples["by_horizon"][horizon]; train, validation, test = parts["train"], parts["validation"], parts["test"]
        x_train, y_train = _arrays(train); x_val, y_val = _arrays(validation); x_test, y_test = _arrays(test)
        point = _fit_xgb(x_train, y_train, seed + horizon); point_path = model_root / f"horizon-{horizon}-point.json"; point.save_model(str(point_path))
        quantiles = {}
        for q in (.1, .5, .9):
            model = _fit_xgb(x_train, y_train, seed + horizon, q); path = model_root / f"horizon-{horizon}-q{int(q * 100)}.json"; model.save_model(str(path)); quantiles[str(q)] = model
        val_predictions = [np.clip(_predict(quantiles[str(q)], x_val), 0, 1) for q in (.1, .5, .9)]
        qhat = max(0.0, fit_cqr(val_predictions[0], val_predictions[2], y_val))
        test_predictions = [np.clip(_predict(quantiles[str(q)], x_test), 0, 1) for q in (.1, .5, .9)]
        lower, median, upper = apply_cqr(*test_predictions, qhat)
        persistence_pred = np.asarray([examples["observations"][sid][idx]["occupancy_rate"] for sid, idx, _, _, _ in test])
        history_pred = _baseline_history(train, test, examples["observations"])
        groups: dict[str, list[int]] = {"city=bengaluru": [], "city=mysuru": [], "city=hubballi-dharwad": [], "city=belagavi": [], "hour=00": []}
        for position, (station_id, index, _, _, _) in enumerate(test):
            station = examples["stations"][station_id]
            dt = examples["observations"][station_id][index]["_dt"]
            capacity = int(station.get("capacity") or 0)
            city = station.get("city", "unknown")
            capacity_bucket = "small_1_4" if capacity <= 4 else "medium_5_12" if capacity <= 12 else "large_13_plus"
            occupancy_bucket = "low_0_33" if y_test[position] < .33 else "mid_33_66" if y_test[position] < .66 else "high_66_100"
            for label in (f"city={city}", f"capacity={capacity_bucket}", f"hour={dt.hour:02d}", f"weekday={dt.weekday()}", f"occupancy={occupancy_bucket}"):
                groups.setdefault(label, []).append(position)
        model_metrics = asdict(metrics(y_test, median)); model_metrics.update({"pinball_q10": pinball_loss(y_test, lower, .1), "pinball_q50": pinball_loss(y_test, median, .5), "pinball_q90": pinball_loss(y_test, upper, .9), "coverage": float(np.mean((y_test >= lower) & (y_test <= upper))), "interval_width": float(np.mean(upper - lower)), "reliability": _reliability(y_test, lower, upper), "persistence_mae": asdict(metrics(y_test, persistence_pred))["mae"], "historical_average_mae": asdict(metrics(y_test, history_pred))["mae"], "slices": _grouped_slice_metrics(y_test, median, groups)})
        metadata = {"horizon": horizon, "version": f"xgb-scenario-v1-h{horizon}", "data_source": "scenario_estimate", "feature_names": FEATURE_NAMES, "qhat": qhat, "metrics": model_metrics, "dataset_hash": dataset_hash, "split_hash": hashlib.sha256(json.dumps(examples["split_metadata"], sort_keys=True).encode()).hexdigest(), "calibration": "validation_only_split_conformal_quantile", "quantiles": ["q10", "q50", "q90"]}
        (model_root / f"horizon-{horizon}-metadata.json").write_text(json.dumps(metadata, indent=2, sort_keys=True), encoding="utf-8"); report["models"][str(horizon)] = metadata
    report_path = output_root / "reports"; report_path.mkdir(parents=True, exist_ok=True); (report_path / "forecast_metrics_scenario_estimate.json").write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    (model_root / "registry.json").write_text(json.dumps({"active": "xgb-scenario-v1", "data_source": "scenario_estimate", "horizons": [1, 2, 3], "feature_names": FEATURE_NAMES, "dataset_hash": dataset_hash}, indent=2, sort_keys=True), encoding="utf-8")
    return report
