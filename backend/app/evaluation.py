"""Deterministic synthetic replay used for local validation and demo metrics."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import json

from .decision import DecisionEngine, erlang_c_wait
from .replay import CITIES, ReplayStore
from .schemas import Point, RecommendRequest


STRATEGIES = ("nearest", "cheapest", "current_availability", "historical_average", "point_forecast", "uncertainty_aware")


def run_synthetic_replay(store: ReplayStore, engine: DecisionEngine, seed: int = 42, steps: int = 48) -> dict:
    rows: dict[str, list[float]] = {strategy: [] for strategy in STRATEGIES}
    decisions = 0
    start = datetime(2026, 1, 5, 6, tzinfo=timezone.utc)
    for step in range(steps):
        at = start + timedelta(hours=step)
        for city in CITIES:
            request = RecommendRequest(origin=Point(lat=city.lat, lon=city.lng), as_of=at, radius_km=25, strategy="uncertainty_aware")
            candidates = engine.candidates(request)
            if not candidates:
                continue
            actual_costs = {}
            for candidate in candidates:
                raw = store.station(candidate.station.id)
                if raw is None:
                    continue
                occupancy = store.occupancy(raw, at + timedelta(hours=1))
                _, wait = erlang_c_wait(raw["capacity"], .75, occupancy * raw["capacity"])
                actual_costs[candidate.station.id] = wait + candidate.route.distance_km
            best = min(actual_costs.values(), default=0)
            decisions += 1
            for strategy in STRATEGIES:
                adjusted = request.model_copy(update={"strategy": strategy, "risk_kappa": 0 if strategy == "point_forecast" else .5})
                labels, _, _ = engine.labels(adjusted)
                choice = labels.get("BALANCED")
                if choice:
                    rows[strategy].append(round(max(0, actual_costs.get(choice.station.id, best) - best), 5))
    metrics = {}
    for strategy, values in rows.items():
        ordered = sorted(values)
        metrics[strategy] = {"mean_regret": round(sum(values) / max(1, len(values)), 5), "median_regret": round(ordered[len(ordered) // 2], 5) if ordered else 0, "p90_regret": round(ordered[min(len(ordered) - 1, int(len(ordered) * .9))], 5) if ordered else 0, "zero_regret_share": round(sum(value == 0 for value in values) / max(1, len(values)), 5), "decisions": len(values)}
    return {"run_id": f"synthetic-replay-{seed}", "seed": seed, "data_source": "synthetic", "steps": steps, "decisions": decisions, "metrics": metrics}


def write_replay_artifact(payload: dict, root: str = "artifacts") -> Path:
    target = Path(root) / "runs" / payload["run_id"]
    target.mkdir(parents=True, exist_ok=True)
    (target / "metrics.json").write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    (target / "config.json").write_text(json.dumps({"seed": payload["seed"], "steps": payload["steps"], "data_source": payload["data_source"]}, indent=2, sort_keys=True), encoding="utf-8")
    return target
