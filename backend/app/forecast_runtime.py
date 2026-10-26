"""Runtime loader for registered scenario XGBoost forecast artifacts."""
from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
import numpy as np

from .ml_features import feature_vector


class ForecastRuntime:
    def __init__(self, root: str | Path):
        import xgboost as xgb
        self.root = Path(root); registry = json.loads((self.root / "registry.json").read_text(encoding="utf-8")); self.version = registry["active"]; self.data_source = registry["data_source"]; self.feature_names = registry["feature_names"]; self.models = {}; self.cache = {}
        for horizon in (1, 2, 3):
            metadata = json.loads((self.root / f"horizon-{horizon}-metadata.json").read_text(encoding="utf-8")); loaded = {}
            for quantile, suffix in ((".1", "q10"), (".5", "q50"), (".9", "q90")):
                model = xgb.Booster(); model.load_model(str(self.root / f"horizon-{horizon}-{suffix}.json")); loaded[quantile] = model
            self.models[horizon] = (loaded, float(metadata["qhat"]))

    def forecast(self, station: dict, rows: list[dict], as_of, horizon: int):
        horizon = max(1, min(3, horizon)); cache_key = (station["id"], as_of.date().isoformat(), as_of.hour, horizon)
        if cache_key in self.cache: return self.cache[cache_key]
        index = min(range(len(rows)), key=lambda position: abs(rows[position]["_dt"] - as_of))
        models, qhat = self.models[horizon]; vector = feature_vector(station, rows, index).reshape(1, -1)
        import xgboost as xgb
        values = [float(np.clip(models[key].predict(xgb.DMatrix(vector))[0], 0, 1)) for key in (".1", ".5", ".9")]
        from .forecasting import apply_cqr
        lower, median, upper = apply_cqr([values[0]], [values[1]], [values[2]], qhat)
        confidence = float(np.clip(1 - (upper[0] - lower[0]), 0, 1))
        label = "high" if confidence >= .75 else "medium" if confidence >= .5 else "low"
        from .schemas import ForecastBand
        result = ForecastBand(horizon_h=horizon, q10=round(float(lower[0]), 3), q50=round(float(median[0]), 3), q90=round(float(upper[0]), 3), confidence=round(confidence, 3), confidence_label=label)
        self.cache[cache_key] = result
        return result
