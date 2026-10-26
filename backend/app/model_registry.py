from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from .forecasting import ForecastMetrics, QuantileForecastModel, XGBoostForecastModel, metrics


def train_synthetic_models(root: str = "artifacts", seed: int = 42) -> dict:
    """Train compact deterministic horizon models on generated feature rows.

    Real dataset adapters can feed the same interface later; the fixture path is
    intentionally usable on a clean machine without a downloaded dataset.
    """
    import numpy as np
    rng = np.random.default_rng(seed)
    features = rng.normal(size=(512, 6)).astype("float32")
    target_base = np.clip(.45 + .12 * features[:, 0] - .05 * features[:, 1] + .03 * features[:, 2], 0, 1)
    output = {"seed": seed, "data_source": "synthetic", "trained_at": datetime.now(timezone.utc).isoformat(), "models": {}}
    for horizon in (1, 2, 3):
        target = np.clip(target_base + (horizon - 1) * .015, 0, 1)
        model = XGBoostForecastModel(horizon, seed).fit(features, target)
        prediction = model.predict(features)
        score = metrics(target, prediction)
        quantile = QuantileForecastModel(horizon, seed).fit(features[:384], target[:384], features[384:], target[384:])
        q10, q50, q90 = quantile.predict(features[384:])
        model_dir = Path(root) / "models" / "synthetic" / f"horizon-{horizon}"
        model_dir.mkdir(parents=True, exist_ok=True)
        metadata = {"horizon": horizon, "version": model.version, "metrics": asdict(score), "uncertainty": {"method": "conformal_quantile", "qhat": quantile.qhat, "mean_interval_width": float(np.mean(q90 - q10)), "median_mae": float(np.mean(np.abs(target[384:] - q50)))}, "config_hash": hashlib.sha256(json.dumps({"seed": seed, "horizon": horizon}, sort_keys=True).encode()).hexdigest()}
        (model_dir / "metadata.json").write_text(json.dumps(metadata, indent=2, sort_keys=True), encoding="utf-8")
        output["models"][str(horizon)] = metadata
    report = Path(root) / "reports"
    report.mkdir(parents=True, exist_ok=True)
    (report / "forecast_metrics.json").write_text(json.dumps(output, indent=2, sort_keys=True), encoding="utf-8")
    return output
