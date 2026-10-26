"""Canonical dataset contracts and offline-first ingestion adapters.

The adapters deliberately do not make operational-status claims.  A dataset
contains station metadata, historical observations and assumptions; the API
labels the source so callers can distinguish it from live data.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from .replay import CITIES, ReplayStore


def _sha(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(raw).hexdigest()


def _utc(value: Any) -> str:
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value).strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _number(value: Any, default: float | None = None) -> float | None:
    try:
        return default if value in (None, "") else float(value)
    except (TypeError, ValueError):
        return default


@dataclass
class QualityReport:
    source: str
    available: bool = True
    rows_received: int = 0
    rows_accepted: int = 0
    errors: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    checks: dict[str, Any] = field(default_factory=dict)

    @property
    def valid(self) -> bool:
        return self.available and not self.errors


@dataclass
class CanonicalDataset:
    source: str
    data_source: str
    stations: list[dict[str, Any]] = field(default_factory=list)
    observations: list[dict[str, Any]] = field(default_factory=list)
    weather: list[dict[str, Any]] = field(default_factory=list)
    prices: list[dict[str, Any]] = field(default_factory=list)
    adjacency: list[dict[str, Any]] = field(default_factory=list)
    splits: dict[str, Any] = field(default_factory=dict)
    quality: QualityReport | None = None
    source_metadata: dict[str, Any] = field(default_factory=dict)

    def content_hash(self) -> str:
        return _sha({"stations": self.stations, "observations": self.observations, "weather": self.weather, "prices": self.prices, "adjacency": self.adjacency, "splits": self.splits})


def _validate_station(row: dict[str, Any], report: QualityReport, index: int) -> dict[str, Any] | None:
    station_id = str(row.get("station_id") or row.get("id") or row.get("external_id") or "").strip()
    lat, lon = _number(row.get("lat", row.get("latitude"))), _number(row.get("lon", row.get("lng", row.get("longitude"))))
    capacity = _number(row.get("capacity", row.get("chargers", row.get("connector_count"))))
    if not station_id:
        report.errors.append({"row": index, "field": "station_id", "message": "missing station identifier"})
        return None
    if lat is None or lon is None or not -90 <= lat <= 90 or not -180 <= lon <= 180:
        report.errors.append({"row": index, "field": "coordinates", "message": "invalid latitude/longitude"})
        return None
    if capacity is None or capacity < 1:
        report.errors.append({"row": index, "field": "capacity", "message": "capacity must be positive"})
        return None
    return {"station_id": station_id, "city": str(row.get("city") or "unknown").strip().lower(), "name": str(row.get("name") or station_id), "address": row.get("address"), "operator": row.get("operator"), "lat": lat, "lon": lon, "capacity": int(capacity), "power_kw": _number(row.get("power_kw", row.get("rated_power_kw"))), "connectors": row.get("connectors") if isinstance(row.get("connectors"), list) else ([str(row["connector_type"])] if row.get("connector_type") else []), "provider": str(row.get("provider") or report.source), "attribution": str(row.get("attribution") or f"Source: {report.source}"), "source_payload": row.get("source_payload") or {}}


def _validate_observation(row: dict[str, Any], station_ids: set[str], report: QualityReport, index: int) -> dict[str, Any] | None:
    station_id = str(row.get("station_id") or row.get("id") or "").strip()
    timestamp = row.get("observed_at", row.get("timestamp", row.get("datetime")))
    occupancy = _number(row.get("occupancy_rate", row.get("occupancy")))
    if station_id not in station_ids:
        report.errors.append({"row": index, "field": "station_id", "message": "observation references unknown station"})
        return None
    try:
        observed_at = _utc(timestamp)
    except (TypeError, ValueError):
        report.errors.append({"row": index, "field": "observed_at", "message": "invalid timestamp"})
        return None
    if occupancy is None or not 0 <= occupancy <= 1:
        report.errors.append({"row": index, "field": "occupancy_rate", "message": "occupancy must be in [0,1]"})
        return None
    return {"station_id": station_id, "observed_at": observed_at, "occupancy_rate": occupancy, "occupied_count": int(_number(row.get("occupied_count"), 0) or 0), "duration_h": _number(row.get("duration_h", row.get("service_duration_h")), 0.5), "volume_kwh": _number(row.get("volume_kwh"), 0), "electricity_price_inr": _number(row.get("electricity_price_inr", row.get("price")), 0), "service_price_inr": _number(row.get("service_price_inr"), 0)}


def _splits(observations: list[dict[str, Any]]) -> dict[str, Any]:
    timestamps = sorted({row["observed_at"] for row in observations})
    if not timestamps:
        return {"train": [], "validation": [], "test": [], "method": "chronological_unique_timestamps"}
    n = len(timestamps)
    train_end, val_end = max(1, int(n * .7)), max(1, int(n * .85))
    train_end, val_end = min(train_end, n), min(max(train_end, val_end), n)
    return {"method": "chronological_unique_timestamps", "train": [timestamps[0], timestamps[train_end - 1]], "validation": [timestamps[train_end], timestamps[val_end - 1]] if val_end > train_end else [], "test": [timestamps[val_end], timestamps[-1]] if val_end < n else [], "hash": _sha(timestamps)}


def _adjacency(stations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for left in stations:
        distances = []
        for right in stations:
            if left["station_id"] == right["station_id"]:
                continue
            lat1, lat2 = math.radians(left["lat"]), math.radians(right["lat"])
            dlat, dlon = lat2 - lat1, math.radians(right["lon"] - left["lon"])
            a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
            distances.append((6371 * 2 * math.asin(math.sqrt(a)), right["station_id"]))
        result.extend({"station_id": left["station_id"], "neighbour_station_id": sid, "distance_km": round(distance, 4)} for distance, sid in sorted(distances)[:3])
    return result


class SyntheticAdapter:
    def __init__(self, seed: int = 20261024, hours: int = 168):
        self.seed, self.hours = seed, hours

    def build(self) -> CanonicalDataset:
        store = ReplayStore(self.seed)
        stations = [{"station_id": s["id"], "city": s["city"], "name": s["name"], "address": s["address"], "operator": "EVision Synthetic", "lat": s["lat"], "lon": s["lng"], "capacity": s["capacity"], "power_kw": s["power_kw"], "connectors": s["connectors"], "provider": "synthetic", "attribution": s["attribution"], "source_payload": {"seed": self.seed}} for s in store.stations]
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        observations, weather, prices = [], [], []
        for hour in range(self.hours):
            at = start + timedelta(hours=hour)
            for station in store.stations:
                rate = round(store.occupancy(station, at), 6)
                observations.append({"station_id": station["id"], "observed_at": at.isoformat().replace("+00:00", "Z"), "occupancy_rate": rate, "occupied_count": round(rate * station["capacity"]), "duration_h": .5, "volume_kwh": round(rate * station["capacity"] * station["power_kw"] * .5, 3), "electricity_price_inr": station["price"], "service_price_inr": 0})
            for city in CITIES:
                weather.append({"city": city.slug, "observed_at": at.isoformat().replace("+00:00", "Z"), "temperature_c": round(25 + 4 * math.sin(hour / 24), 3), "precipitation_mm": 0, "humidity_pct": 65, "source": "synthetic"})
        quality = QualityReport("synthetic", rows_received=len(stations) + len(observations), rows_accepted=len(stations) + len(observations), checks={"deterministic_seed": self.seed, "hourly": True})
        dataset = CanonicalDataset("synthetic", "synthetic", stations, observations, weather, prices, _adjacency(stations), _splits(observations), quality, {"seed": self.seed, "generator": "ReplayStore"})
        return dataset


def _records(path: Path) -> list[dict[str, Any]]:
    if path.is_dir():
        files = sorted(path.glob("*.json")) + sorted(path.glob("*.csv"))
        if not files:
            return []
        records: list[dict[str, Any]] = []
        for file in files:
            records.extend(_records(file))
        return records
    if path.suffix.lower() == ".csv":
        with path.open(newline="", encoding="utf-8-sig") as handle:
            return list(csv.DictReader(handle))
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return payload
    for key in ("records", "stations", "observations", "data", "results"):
        if isinstance(payload.get(key), list):
            return payload[key]
    return []


class FileDatasetAdapter:
    def __init__(self, source: str, path: str | Path | None):
        self.source, self.path = source, Path(path) if path else None

    def build(self) -> CanonicalDataset:
        if not self.path or not self.path.exists():
            quality = QualityReport(self.source, available=False, warnings=["Dataset files are not available; provide --input or download the source files."])
            return CanonicalDataset(self.source, self.source, quality=quality, source_metadata={"input": str(self.path) if self.path else None})
        rows = _records(self.path)
        report = QualityReport(self.source, rows_received=len(rows))
        stations, observations, weather = [], [], []
        for index, row in enumerate(rows):
            has_coordinates = row.get("lat", row.get("latitude", row.get("lon", row.get("lng", row.get("longitude"))))) not in (None, "")
            has_capacity = row.get("capacity", row.get("chargers", row.get("connector_count"))) not in (None, "")
            if has_coordinates and has_capacity:
                station = _validate_station(row, report, index)
                if station:
                    stations.append(station)
        station_ids = {row["station_id"] for row in stations}
        for index, row in enumerate(rows):
            if row.get("observed_at") or row.get("timestamp") or row.get("datetime"):
                if row.get("temperature_c") is not None or row.get("temperature") is not None:
                    try:
                        weather.append({"city": str(row.get("city") or "unknown").lower(), "observed_at": _utc(row.get("observed_at", row.get("timestamp", row.get("datetime")))), "temperature_c": _number(row.get("temperature_c", row.get("temperature")), 0), "precipitation_mm": _number(row.get("precipitation_mm", row.get("precipitation")), 0), "humidity_pct": _number(row.get("humidity_pct", row.get("humidity")), 0), "source": self.source})
                    except (TypeError, ValueError):
                        report.errors.append({"row": index, "field": "observed_at", "message": "invalid weather timestamp"})
                else:
                    observation = _validate_observation(row, station_ids, report, index)
                    if observation:
                        observations.append(observation)
        report.rows_accepted = len(stations) + len(observations)
        report.checks = {"duplicate_station_ids": len(station_ids) != len(stations), "hourly": self._hourly(observations)}
        if not observations:
            report.warnings.append("No historical observations found; forecasting cannot be trained from this source.")
        dataset = CanonicalDataset(self.source, self.source, stations, observations, weather, [], _adjacency(stations), _splits(observations), report, {"input": str(self.path), "input_sha256": hashlib.sha256(self.path.read_bytes()).hexdigest() if self.path.is_file() else None})
        return dataset

    @staticmethod
    def _hourly(rows: list[dict[str, Any]]) -> bool:
        by_station: dict[str, list[datetime]] = {}
        for row in rows:
            by_station.setdefault(row["station_id"], []).append(datetime.fromisoformat(row["observed_at"].replace("Z", "+00:00")))
        return all((b - a).total_seconds() == 3600 for values in by_station.values() for a, b in zip(sorted(values), sorted(values)[1:]))


class OpenChargeMapAdapter:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def build(self) -> CanonicalDataset:
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        records = payload.get("records", payload if isinstance(payload, list) else [])
        report = QualityReport("open_charge_map", rows_received=len(records))
        stations = []
        for index, item in enumerate(records):
            address = item.get("AddressInfo") or item.get("address_info") or {}
            connectors = item.get("Connections") or item.get("connectors") or []
            row = {"station_id": item.get("ID") or item.get("id"), "city": item.get("evision_city") or address.get("Town") or "unknown", "name": address.get("Title") or item.get("name"), "address": address.get("AddressLine1") or address.get("AddressLine2"), "operator": (item.get("OperatorInfo") or {}).get("Title") if isinstance(item.get("OperatorInfo"), dict) else item.get("operator"), "lat": address.get("Latitude") or item.get("lat"), "lon": address.get("Longitude") or item.get("lon"), "capacity": item.get("NumberOfPoints") or len(connectors) or 1, "power_kw": None, "connectors": [str((connection.get("ConnectionType") or {}).get("Title") or connection.get("connector_type") or "unknown") for connection in connectors if isinstance(connection, dict)], "provider": "open_charge_map", "attribution": "Location metadata © Open Charge Map contributors; occupancy is not provided by OCM.", "source_payload": item}
            station = _validate_station(row, report, index)
            if station:
                stations.append(station)
        report.rows_accepted = len(stations)
        return CanonicalDataset("open_charge_map", "open_charge_map", stations=stations, adjacency=_adjacency(stations), quality=report, source_metadata={"input": str(self.path), "occupancy": "not_provided"})


def write_dataset(dataset: CanonicalDataset, root: str | Path) -> Path:
    target = Path(root) / dataset.source
    target.mkdir(parents=True, exist_ok=True)
    for name, rows in (("stations.json", dataset.stations), ("observations.jsonl", dataset.observations), ("weather.jsonl", dataset.weather), ("prices.jsonl", dataset.prices), ("adjacency.json", dataset.adjacency)):
        path = target / name
        if name.endswith(".jsonl"):
            path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8")
        else:
            path.write_text(json.dumps(rows, indent=2, sort_keys=True), encoding="utf-8")
    (target / "splits.json").write_text(json.dumps(dataset.splits, indent=2, sort_keys=True), encoding="utf-8")
    metadata = {"source": dataset.source, "data_source": dataset.data_source, "content_sha256": dataset.content_hash(), "source_metadata": dataset.source_metadata, "row_counts": {"stations": len(dataset.stations), "observations": len(dataset.observations), "weather": len(dataset.weather), "prices": len(dataset.prices), "adjacency": len(dataset.adjacency)}}
    (target / "metadata.json").write_text(json.dumps(metadata, indent=2, sort_keys=True), encoding="utf-8")
    quality_payload = asdict(dataset.quality or QualityReport(dataset.source))
    if dataset.source_metadata.get("per_city_reports"):
        quality_payload["per_city"] = dataset.source_metadata["per_city_reports"]
    quality_payload["missing_field_policy"] = dataset.source_metadata.get("missing_field_policy", {})
    (target / "quality_report.json").write_text(json.dumps(quality_payload, indent=2, sort_keys=True), encoding="utf-8")
    return target
