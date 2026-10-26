"""Run deterministic, leakage-safe baseline benchmarks on UrbanEV and CHARGED.

The reference datasets are used for measured benchmarking only. They are not
used as Karnataka production data and are never relabelled as scenario data.
The reader streams the canonical JSONL files and keeps only compact running
statistics, so the multi-million-row files remain usable on a laptop.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


def _time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


class Accumulator:
    def __init__(self) -> None:
        self.n = 0
        self.abs_error = 0.0
        self.squared_error = 0.0
        self.smape = 0.0
        self.actual_sum = 0.0
        self.actual_squared_sum = 0.0

    def add(self, actual: float, predicted: float) -> None:
        error = float(predicted) - float(actual)
        self.n += 1
        self.abs_error += abs(error)
        self.squared_error += error * error
        self.smape += abs(error) / max(1e-6, (abs(actual) + abs(predicted)) / 2.0)
        self.actual_sum += actual
        self.actual_squared_sum += actual * actual

    def result(self) -> dict[str, float | int]:
        if not self.n:
            return {"count": 0, "mae": 0.0, "rmse": 0.0, "smape": 0.0, "r2": 0.0}
        denominator = self.n * self.actual_squared_sum - self.actual_sum * self.actual_sum
        r2 = 1.0 - (self.squared_error / denominator * self.n if denominator else 0.0)
        return {"count": self.n, "mae": self.abs_error / self.n, "rmse": (self.squared_error / self.n) ** .5, "smape": self.smape / self.n, "r2": r2}


def _city_slice(station: dict[str, Any], dt: datetime, actual: float) -> tuple[str, ...]:
    capacity = int(station.get("capacity") or 0)
    capacity_bucket = "small_1_4" if capacity <= 4 else "medium_5_12" if capacity <= 12 else "large_13_plus"
    occupancy_bucket = "low_0_33" if actual < .33 else "mid_33_66" if actual < .66 else "high_66_100"
    return (f"city={station.get('city', 'unknown')}", f"capacity={capacity_bucket}", f"hour={dt.hour:02d}", f"weekday={dt.weekday()}", f"occupancy={occupancy_bucket}")


def benchmark_dataset(root: Path, max_points: int, max_rows: int | None = None) -> dict[str, Any]:
    stations = {row["station_id"]: row for row in json.loads((root / "stations.json").read_text(encoding="utf-8"))}
    histories: dict[tuple[str, int, int], tuple[float, int]] = {}
    pending: dict[str, list[tuple[datetime, float, float, int]]] = defaultdict(list)
    scores: dict[int, dict[str, Accumulator]] = {h: {"persistence": Accumulator(), "historical_average": Accumulator()} for h in (1, 2, 3)}
    slices: dict[int, dict[str, dict[str, Accumulator]]] = {h: {"persistence": {}, "historical_average": {}} for h in (1, 2, 3)}
    seen = 0
    cities = sorted({str(row.get("city", "unknown")) for row in stations.values()})
    per_city_limit = max(1, max_points // max(1, len(cities)))
    sampled_by_city: dict[str, int] = defaultdict(int)
    scanned_rows = 0

    with (root / "observations.jsonl").open(encoding="utf-8") as handle:
        for line in handle:
            if max_rows is not None and scanned_rows >= max_rows:
                break
            scanned_rows += 1
            row = json.loads(line)
            station = stations.get(row.get("station_id"))
            if station is None:
                continue
            dt = _time(row["observed_at"])
            actual = float(row.get("occupancy_rate") or 0.0)
            station_id = row["station_id"]
            current_pending = pending[station_id]
            remaining: list[tuple[datetime, float, float, int]] = []
            for target, persistence, historical, horizon in current_pending:
                if target == dt:
                    seen += 1
                    token = f"{station_id}|{dt.isoformat()}|{horizon}".encode()
                    keep = int.from_bytes(hashlib.blake2b(token, digest_size=4).digest(), "big") % 100 < 20
                    city = str(station.get("city", "unknown"))
                    if keep and sampled_by_city[city] < per_city_limit:
                        sampled_by_city[city] += 1
                        scores[horizon]["persistence"].add(actual, persistence)
                        scores[horizon]["historical_average"].add(actual, historical)
                        for label in _city_slice(station, dt, actual):
                            for name, prediction in (("persistence", persistence), ("historical_average", historical)):
                                accumulator = slices[horizon][name].setdefault(label, Accumulator())
                                accumulator.add(actual, prediction)
                elif target > dt:
                    remaining.append((target, persistence, historical, horizon))
            pending[station_id] = remaining

            for horizon in (1, 2, 3):
                key = (station_id, (dt + timedelta(hours=horizon)).weekday(), (dt + timedelta(hours=horizon)).hour)
                total, count = histories.get(key, (0.0, 0))
                historical = total / count if count else actual
                pending[station_id].append((dt + timedelta(hours=horizon), actual, historical, horizon))
            key = (station_id, dt.weekday(), dt.hour)
            total, count = histories.get(key, (0.0, 0))
            histories[key] = (total + actual, count + 1)

    return {
        "data_source": root.name,
        "dataset_root": str(root),
        "sampling": {"method": "deterministic_blake2b_20_percent_with_city_quota", "max_points": max_points, "per_city_limit": per_city_limit, "max_rows": max_rows, "scanned_rows": scanned_rows, "matched_predictions": seen, "sampled_points": int(sum(sampled_by_city.values())), "sampled_points_by_city": dict(sorted(sampled_by_city.items()))},
        "horizons": {str(h): {name: accumulator.result() for name, accumulator in scores[h].items()} for h in (1, 2, 3)},
        "slices": {str(h): {name: {label: accumulator.result() for label, accumulator in sorted(groups.items())} for name, groups in methods.items()} for h, methods in slices.items()},
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--datasets", default="artifacts/datasets")
    parser.add_argument("--output", default="artifacts/reports/reference_benchmarks.json")
    parser.add_argument("--max-points", type=int, default=150000)
    parser.add_argument("--max-rows", type=int, default=2000000, help="Bound the local scan; use 0 for the full files.")
    parser.add_argument("--dataset", choices=("urban_ev", "charged", "all"), default="all")
    args = parser.parse_args()
    root = Path(args.datasets)
    max_rows = None if args.max_rows == 0 else args.max_rows
    names = ("urban_ev", "charged") if args.dataset == "all" else (args.dataset,)
    report = {name: benchmark_dataset(root / name, args.max_points, max_rows) for name in names}
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({name: value["sampling"] for name, value in report.items()}, sort_keys=True))


if __name__ == "__main__":
    main()
