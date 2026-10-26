# Phase 5 — Forecasting and AI Model Checklist

Last verified: 2026-10-26

## Training pipeline

- [x] Canonical feature extraction from `karnataka_scenario`.
- [x] Occupancy lags: 1h, 2h, 3h, 6h, 12h, 24h, 168h.
- [x] Historical-only rolling mean and standard deviation.
- [x] Hour/day cyclic features and weekend flag.
- [x] Station capacity, power, coordinates, city code, and station metadata.
- [x] Price, duration, and volume feature columns with explicit missing-value handling.
- [x] Chronological train/validation/test split.
- [x] Features use only observations at or before the decision timestamp.
- [x] Deterministic feature, dataset, and split hashes.

## Models

- [x] Persistence baseline.
- [x] Historical hour-of-week baseline.
- [x] XGBoost point models for horizons 1, 2, and 3.
- [x] Native XGBoost quantile models for q10, q50, and q90.
- [x] Quantile ordering and `[0,1]` clipping.
- [x] Validation-only split conformal calibration.
- [x] Confidence score and high/medium/low labels.
- [x] Deterministic fallback when registered artifacts are unavailable.

## Evaluation and artifacts

- [x] MAE, RMSE, sMAPE, and R².
- [x] Pinball loss for all three quantiles.
- [x] Coverage and interval width.
- [x] Reliability data.
- [x] Dataset/split/model metadata and feature schema.
- [x] Saved point and quantile model artifacts for all horizons.
- [x] Model registry with active scenario model.
- [x] Calibration parameters and runtime version metadata.
- [ ] Full city/capacity/hour/occupancy slice report.
- [ ] Dedicated UrbanEV reference benchmark report.
- [ ] Dedicated CHARGED reference benchmark report.

## Backend integration

- [x] Registered model loaded at backend startup.
- [x] Forecast runtime uses q10/q50/q90 artifacts.
- [x] Queueing/recommendation layer receives model forecast bands.
- [x] Model version, data source, confidence, and calibration are exposed.
- [x] Model artifact readiness status is exposed.
- [x] Fallback is logged and remains deterministic.
- [x] Evaluation reveal remains separate from decision-time forecast.

## Verification

- [x] Feature and artifact tests pass.
- [x] Quantile ordering test passes.
- [x] Runtime model load smoke test passes.
- [x] API/scenario tests pass after runtime integration.
- [x] Training completed for 180,000 deterministic examples.
- [x] Three horizons and nine quantile/point artifacts generated.
- [x] Full backend suite: 22 tests passed.

## Current artifacts

- Production source: `scenario_estimate` / `karnataka_scenario`.
- Active model: `xgb-scenario-v1`.
- Horizons: 1h, 2h, 3h.
- Quantiles: q10, q50, q90.
- Model directory: `backend/artifacts/models/scenario_estimate/`.
- Metrics report: `backend/artifacts/reports/forecast_metrics_scenario_estimate.json`.

The unchecked benchmark and slice-report tasks are non-blocking for the active
four-city forecast path but remain visible so Phase 5 is not misrepresented as
fully complete.
