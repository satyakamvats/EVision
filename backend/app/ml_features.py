"""Leakage-safe feature construction for station occupancy forecasting."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

FEATURE_NAMES = [
    "hour_sin", "hour_cos", "dow_sin", "dow_cos", "is_weekend",
    "capacity", "power_kw", "lat", "lon", "city_code",
    "occupancy_lag_1h", "occupancy_lag_2h", "occupancy_lag_3h",
    "occupancy_lag_6h", "occupancy_lag_12h", "occupancy_lag_24h",
    "occupancy_lag_168h", "rolling_mean_6h", "rolling_std_6h",
    "price", "duration_h", "volume_kwh",
]
CITY_CODES = {"bengaluru": 0.0, "mysuru": 1.0, "hubballi-dharwad": 2.0, "belagavi": 3.0}


def parse_time(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def load_scenario(root: str | Path) -> tuple[dict[str, dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    root = Path(root)
    stations = {row["station_id"]: row for row in json.loads((root / "stations.json").read_text(encoding="utf-8"))}
    observations: dict[str, list[dict[str, Any]]] = {key: [] for key in stations}
    with (root / "observations.jsonl").open(encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            if row["station_id"] in observations:
                row["_dt"] = parse_time(row["observed_at"])
                observations[row["station_id"]].append(row)
    for rows in observations.values():
        rows.sort(key=lambda row: row["_dt"])
    return stations, observations


def feature_vector(station: dict[str, Any], rows: list[dict[str, Any]], index: int) -> np.ndarray:
    row = rows[index]
    dt = row["_dt"]
    def lag(hours: int) -> float:
        position = index - hours
        return float(rows[position]["occupancy_rate"]) if position >= 0 else float(row["occupancy_rate"])
    window = [float(rows[position]["occupancy_rate"]) for position in range(max(0, index - 5), index + 1)]
    values = [
        np.sin(2 * np.pi * dt.hour / 24), np.cos(2 * np.pi * dt.hour / 24),
        np.sin(2 * np.pi * dt.weekday() / 7), np.cos(2 * np.pi * dt.weekday() / 7),
        float(dt.weekday() >= 5), float(station["capacity"]), float(station.get("power_kw") or 22),
        float(station["lat"]), float(station.get("lon", station.get("lng"))), CITY_CODES.get(station.get("city", ""), -1.0),
        lag(1), lag(2), lag(3), lag(6), lag(12), lag(24), lag(168),
        float(np.mean(window)), float(np.std(window)), float(row.get("electricity_price_inr", 0)),
        float(row.get("duration_h", 0)), float(row.get("volume_kwh", 0)),
    ]
    return np.asarray(values, dtype=np.float32)


def build_examples(root: str | Path, max_rows: int | None = None, seed: int = 42) -> dict[str, Any]:
    stations, observations = load_scenario(root)
    examples: list[tuple[str, int, int, np.ndarray, float]] = []
    for station_id in sorted(stations):
        rows = observations[station_id]
        for index in range(168, max(168, len(rows) - 3)):
            for horizon in (1, 2, 3):
                if index + horizon < len(rows):
                    examples.append((station_id, index, horizon, feature_vector(stations[station_id], rows, index), float(rows[index + horizon]["occupancy_rate"])))
    if max_rows and len(examples) > max_rows:
        rng = np.random.default_rng(seed)
        selected = np.sort(rng.choice(len(examples), size=max_rows, replace=False))
        examples = [examples[int(index)] for index in selected]
    timestamps = [observations[station_id][index]["_dt"] for station_id, index, _, _, _ in examples]
    ordered = sorted(timestamps)
    train_cut = ordered[int(len(ordered) * .70)]
    validation_cut = ordered[int(len(ordered) * .85)]
    result: dict[str, Any] = {"feature_names": FEATURE_NAMES, "stations": stations, "observations": observations, "by_horizon": {}}
    for horizon in (1, 2, 3):
        rows = [item for item in examples if item[2] == horizon]
        train, validation, test = [], [], []
        for item in rows:
            timestamp = observations[item[0]][item[1]]["_dt"]
            target = train if timestamp < train_cut else validation if timestamp < validation_cut else test
            target.append(item)
        result["by_horizon"][horizon] = {"train": train, "validation": validation, "test": test}
    result["split_metadata"] = {"method": "chronological_as_of_timestamp", "train_end_exclusive": train_cut.isoformat(), "validation_end_exclusive": validation_cut.isoformat(), "example_count": len(examples)}
    return result
