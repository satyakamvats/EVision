"""Build clearly labelled Karnataka scenario data from reference demand shapes.

UrbanEV and CHARGED are not Karnataka observations.  This module extracts
hour-of-day demand shapes from them, then applies those shapes to the real
Karnataka station catalogue from OCM.  Outputs are ``scenario_estimate`` and
must never be presented as observed live availability.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable
import random

from .data_layer import CanonicalDataset, OpenChargeMapAdapter, QualityReport, _adjacency, _sha, _splits


def _profile_from_wide_csv(path: Path, *, values_are_counts: bool = False) -> list[float]:
    buckets: list[list[float]] = [[] for _ in range(24)]
    with path.open(newline="", encoding="utf-8-sig") as handle:
        for index, row in enumerate(csv.reader(handle)):
            if index == 0 or not row or not row[0].strip():
                continue
            try:
                hour = datetime.fromisoformat(row[0].strip()).hour
            except ValueError:
                continue
            values = []
            for value in row[1:]:
                try:
                    number = float(value)
                    if math.isfinite(number):
                        values.append(number)
                except ValueError:
                    continue
            if values:
                level = sum(values) / len(values)
                buckets[hour].append(level)
    means = [sum(values) / len(values) if values else 0.0 for values in buckets]
    if values_are_counts:
        maximum = max(means or [1.0])
        means = [value / maximum if maximum else 0.0 for value in means]
    elif max(means or [0]) > 1.0:
        means = [value / 100.0 for value in means]
    average = sum(means) / max(1, len(means))
    return [min(0.95, max(0.03, 0.25 + (value - average) * 0.9)) for value in means]


def discover_reference_profiles(root: str | Path) -> tuple[list[float], dict[str, list[float]], dict[str, str]]:
    root = Path(root)
    urban = next(root.glob("urban_ev/**/zone-cleaned-aggregated/charge_1hour/occupancy.csv"), None)
    urban_profile = _profile_from_wide_csv(urban) if urban else [0.25] * 24
    charged_profiles: dict[str, list[float]] = {}
    for city in ("AMS", "JHB", "LOA", "MEL", "SPO", "SZH"):
        duration = root / "charged" / "CHARGED" / city / "duration.csv"
        if duration.exists():
            charged_profiles[city] = _profile_from_wide_csv(duration, values_are_counts=True)
    sources = {"urban_ev": str(urban) if urban else "unavailable", "charged": ",".join(sorted(charged_profiles)) or "unavailable"}
    return urban_profile, charged_profiles, sources


def build_karnataka_scenario(ocm_path: str | Path, raw_root: str | Path, seed: int = 20261024, days: int = 30) -> CanonicalDataset:
    catalogue = OpenChargeMapAdapter(ocm_path).build()
    urban_profile, charged_profiles, sources = discover_reference_profiles(raw_root)
    city_names = {"bengaluru", "mysuru", "hubballi-dharwad", "belagavi"}
    aliases = {"hubballi": "hubballi-dharwad", "dharwad": "hubballi-dharwad", "hubballi–dharwad": "hubballi-dharwad"}
    stations = []
    for station in catalogue.stations:
        city = aliases.get(station["city"], station["city"])
        if city in city_names:
            station = {**station, "city": city, "provider": "open_charge_map", "attribution": "Location metadata © Open Charge Map contributors; occupancy is a reference-calibrated scenario estimate."}
            stations.append(station)
    reference_profiles = list(charged_profiles.values())
    if reference_profiles:
        blended_profile = [sum([urban_profile[hour]] + [profile[hour] for profile in reference_profiles]) / (1 + len(reference_profiles)) for hour in range(24)]
    else:
        blended_profile = urban_profile
    city_multipliers = {"bengaluru": 1.12, "mysuru": .86, "hubballi-dharwad": .94, "belagavi": .90}
    rng = random.Random(seed)
    observations, weather = [], []
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    city_offsets = {"bengaluru": .08, "mysuru": -.03, "hubballi-dharwad": .02, "belagavi": -.01}
    for hour_index in range(days * 24):
        at = start + timedelta(hours=hour_index)
        base = blended_profile[at.hour]
        for station in stations:
            city = station["city"]
            # Seeded noise creates realistic station/day variation while keeping
            # the generated fixture exactly reproducible.
            day_effect = .04 if at.weekday() < 5 else -.03
            noise = rng.uniform(-.025, .025)
            offset = city_offsets.get(city, 0) + ((sum(ord(c) for c in station["station_id"]) % 11) - 5) / 100
            occupancy = min(.95, max(.02, base * city_multipliers.get(city, 1.0) + offset + day_effect + noise))
            observations.append({"station_id": station["station_id"], "observed_at": at.isoformat().replace("+00:00", "Z"), "occupancy_rate": round(occupancy, 6), "occupied_count": round(occupancy * station["capacity"]), "duration_h": .5, "volume_kwh": round(occupancy * station["capacity"] * (station["power_kw"] or 22) * .5, 3), "electricity_price_inr": 16.0, "service_price_inr": 0.0})
        for city in ("bengaluru", "mysuru", "hubballi-dharwad", "belagavi"):
            weather.append({"city": city, "observed_at": at.isoformat().replace("+00:00", "Z"), "temperature_c": round(25 + 4 * math.sin(hour_index / 24), 3), "precipitation_mm": 0.0, "humidity_pct": 65.0, "source": "scenario_estimate"})
    quality = QualityReport("karnataka_scenario", len(stations) + len(observations), len(stations) + len(observations), checks={"reference_only": True, "cities": ["bengaluru", "mysuru", "hubballi-dharwad", "belagavi"]}, warnings=["Occupancy is a reference-calibrated scenario estimate, not Karnataka observed data."])
    metadata = {"seed": seed, "reference_sources": sources, "reference_blend": ["UrbanEV"] + sorted(charged_profiles), "station_metadata_source": "open_charge_map", "occupancy_semantics": "scenario_estimate", "fabrication_method": "hourly reference-profile blend + city multipliers + seeded station/day noise", "real_observations": False}
    return CanonicalDataset("karnataka_scenario", "scenario_estimate", stations, observations, weather, [], _adjacency(stations), _splits(observations), quality, metadata)
