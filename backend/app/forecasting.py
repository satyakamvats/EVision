"""Small, reproducible forecasting primitives used by the API and replay code."""

from dataclasses import dataclass
from datetime import datetime
from math import sqrt
from typing import Iterable

import numpy as np


@dataclass(frozen=True)
class ForecastMetrics:
    mae: float
    rmse: float
    smape: float
    r2: float


def persistence(values: Iterable[float], horizon: int = 1) -> np.ndarray:
    array = np.asarray(list(values), dtype=float)
    if array.size == 0:
        return array
    return np.roll(array, horizon)


def historical_average(rows: Iterable[tuple[datetime, float]], target: datetime) -> float:
    values = [value for at, value in rows if at < target and at.weekday() == target.weekday() and at.hour == target.hour]
    return float(np.mean(values)) if values else 0.5


def metrics(actual: Iterable[float], predicted: Iterable[float]) -> ForecastMetrics:
    y = np.asarray(list(actual), dtype=float)
    p = np.asarray(list(predicted), dtype=float)
    if y.size == 0 or y.size != p.size:
        raise ValueError("actual and predicted must have the same non-zero length")
    error = y - p
    denominator = np.maximum(np.abs(y) + np.abs(p), 1e-8)
    ss_total = float(np.sum((y - np.mean(y)) ** 2))
    r2 = 1.0 - float(np.sum(error**2)) / ss_total if ss_total else 0.0
    return ForecastMetrics(float(np.mean(np.abs(error))), sqrt(float(np.mean(error**2))), float(np.mean(2 * np.abs(error) / denominator)), r2)


def pinball_loss(actual: Iterable[float], predicted: Iterable[float], quantile: float) -> float:
    y = np.asarray(list(actual), dtype=float)
    p = np.asarray(list(predicted), dtype=float)
    error = y - p
    return float(np.mean(np.maximum(quantile * error, (quantile - 1) * error)))


def fit_cqr(lower: Iterable[float], upper: Iterable[float], actual: Iterable[float], alpha: float = .2) -> float:
    lo = np.asarray(list(lower), dtype=float)
    hi = np.asarray(list(upper), dtype=float)
    y = np.asarray(list(actual), dtype=float)
    if not (len(lo) == len(hi) == len(y)) or len(y) == 0:
        raise ValueError("CQR arrays must have the same non-zero length")
    scores = np.maximum(lo - y, y - hi)
    rank = min(len(scores) - 1, max(0, int(np.ceil((len(scores) + 1) * (1 - alpha)) - 1)))
    return float(np.partition(scores, rank)[rank])


def apply_cqr(lower: Iterable[float], median: Iterable[float], upper: Iterable[float], qhat: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    lo = np.asarray(list(lower), dtype=float) - qhat
    mid = np.asarray(list(median), dtype=float)
    hi = np.asarray(list(upper), dtype=float) + qhat
    stacked = np.sort(np.vstack([lo, mid, hi]), axis=0)
    return np.clip(stacked[0], 0, 1), np.clip(stacked[1], 0, 1), np.clip(stacked[2], 0, 1)


class XGBoostForecastModel:
    """Optional XGBoost model with a deterministic mean fallback.

    The fallback keeps API/demo operation available when the optional xgboost
    wheel is not installed; production training reports that fallback clearly.
    """

    def __init__(self, horizon: int, seed: int = 42):
        self.horizon = horizon
        self.seed = seed
        self.model = None
        self.version = f"baseline-mean-h{horizon}"

    def fit(self, features: np.ndarray, target: np.ndarray) -> "XGBoostForecastModel":
        try:
            from xgboost import XGBRegressor
        except ImportError:
            self.model = (float(np.mean(target)),)
            return self
        self.model = XGBRegressor(n_estimators=150, max_depth=5, learning_rate=0.05, subsample=0.9, colsample_bytree=0.9, objective="reg:squarederror", random_state=self.seed, n_jobs=1)
        self.model.fit(features, target)
        self.version = f"xgb-h{self.horizon}"
        return self

    def predict(self, features: np.ndarray) -> np.ndarray:
        if self.model is None:
            raise RuntimeError("Model has not been fitted")
        if isinstance(self.model, tuple):
            return np.full(len(features), self.model[0])
        return np.clip(np.asarray(self.model.predict(features), dtype=float), 0, 1)


class QuantileForecastModel:
    """Quantile wrapper with a conformal calibration surface."""

    def __init__(self, horizon: int, seed: int = 42):
        self.horizon = horizon
        self.seed = seed
        self.point = XGBoostForecastModel(horizon, seed)
        self.qhat = 0.0

    def fit(self, features: np.ndarray, target: np.ndarray, calibration_features: np.ndarray | None = None, calibration_target: np.ndarray | None = None) -> "QuantileForecastModel":
        self.point.fit(features, target)
        if calibration_features is not None and calibration_target is not None:
            median = self.point.predict(calibration_features)
            self.qhat = fit_cqr(median - .08, median + .08, calibration_target)
        return self

    def predict(self, features: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        median = self.point.predict(features)
        return apply_cqr(median - .08, median, median + .08, self.qhat)
