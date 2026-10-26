"""Format-specific readers for the downloaded UrbanEV and CHARGED datasets."""
from __future__ import annotations

import csv
import hashlib
import json
from collections import defaultdict
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .data_layer import CanonicalDataset, QualityReport, _adjacency, _number, _sha, _splits


def _read_wide(path: Path) -> tuple[list[str], Iterator[tuple[str, list[str]]]]:
    handle = path.open(newline="", encoding="utf-8-sig")
    reader = csv.reader(handle)
    header = next(reader, [])
    def rows():
        try:
            for row in reader:
                if row:
                    yield (row[0], row[1:])
        finally:
            handle.close()
    return header[1:], rows()


class UrbanEVAdapter:
    source = "urban_ev"

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.dataset_root = next((p for p in self.root.rglob("20220901-20230228_station-raw") if p.is_dir()), self.root)
        self.zone_root = next((p for p in self.root.rglob("20220901-20230228_zone-cleaned-aggregated") if p.is_dir()), self.root)

    def build(self) -> CanonicalDataset:
        info_path = self.dataset_root / "station_information.csv"
        occupancy_path = self.zone_root / "charge_1hour" / "occupancy.csv"
        report = QualityReport(self.source)
        if not info_path.exists() or not occupancy_path.exists():
            report.available = False
            report.warnings.append("UrbanEV station_information.csv or hourly occupancy.csv is missing")
            return CanonicalDataset(self.source, self.source, quality=report)
        stations, by_zone = [], defaultdict(list)
        with info_path.open(newline="", encoding="utf-8-sig") as handle:
            for index, row in enumerate(csv.DictReader(handle)):
                lat, lon = _number(row.get("latitude")), _number(row.get("longitude"))
                capacity = int(_number(row.get("charge_count"), 1) or 1)
                if lat is None or lon is None or not -90 <= lat <= 90 or not -180 <= lon <= 180:
                    report.errors.append({"row": index, "field": "coordinates", "message": "invalid UrbanEV coordinate"})
                    continue
                station = {"station_id": f"urban_ev:{row['station_id']}", "city": "shenzhen_reference", "name": f"UrbanEV station {row['station_id']}", "address": "Shenzhen reference dataset", "operator": "UrbanEV", "lat": lat, "lon": lon, "capacity": capacity, "power_kw": None, "connectors": [], "provider": "urban_ev", "attribution": "UrbanEV reference dataset; not Karnataka operational data.", "source_payload": {"TAZID": row.get("TAZID")}}
                stations.append(station)
                by_zone[str(row.get("TAZID"))].append(station)
        zone_columns, zone_rows = _read_wide(occupancy_path)
        column_index = {str(value): index for index, value in enumerate(zone_columns)}
        observations = []
        report.rows_received = len(stations)
        for timestamp, values in zone_rows:
            try:
                observed_at = datetime.fromisoformat(timestamp.replace("Z", "+00:00")).replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
            except ValueError:
                report.errors.append({"field": "time", "message": "invalid timestamp", "value": timestamp})
                continue
            for zone, zone_stations in by_zone.items():
                position = column_index.get(zone)
                occupied = _number(values[position], 0) if position is not None and position < len(values) else 0
                total_capacity = sum(item["capacity"] for item in zone_stations) or 1
                rate = min(1.0, max(0.0, (occupied or 0) / total_capacity))
                for station in zone_stations:
                    observations.append({"station_id": station["station_id"], "observed_at": observed_at, "occupancy_rate": rate, "occupied_count": round(rate * station["capacity"]), "duration_h": .5, "volume_kwh": 0.0, "electricity_price_inr": 0.0, "service_price_inr": 0.0})
        report.rows_accepted = len(stations) + len(observations)
        report.checks = {"station_count": len(stations), "hourly_source": True, "coordinate_validation": True, "mapped_zones": len(by_zone)}
        report.warnings.append("UrbanEV occupancy is zone-aggregated and mapped to stations by TAZID; it is Shenzhen reference data, not Karnataka observation data.")
        return CanonicalDataset(self.source, self.source, stations, observations, [], [], _adjacency(stations), _splits(observations), report, {"raw_format": "station_information + zone hourly wide occupancy", "input_root": str(self.root), "missing_field_policy": {"weather": "unavailable_in_downloaded_archive", "station_prices": "preserve_zero_and_report_missing", "connectors": "empty_when_not_provided"}, "reference_geography": "Shenzhen; never relabel as Karnataka"})


class ChargedAdapter:
    source = "charged"

    def __init__(self, root: str | Path):
        self.root = Path(root)

    def build(self) -> CanonicalDataset:
        base = self.root / "CHARGED" if (self.root / "CHARGED").exists() else self.root
        stations, observations, weather = [], [], []
        report = QualityReport(self.source)
        for city_dir in sorted(p for p in base.iterdir() if p.is_dir() and not p.name.endswith("_remove_zero")):
            station_path, duration_path, weather_path = city_dir / "stations.csv", city_dir / "duration.csv", city_dir / "weather.csv"
            if not station_path.exists() or not duration_path.exists():
                report.warnings.append(f"{city_dir.name}: stations.csv or duration.csv missing")
                continue
            city_stations = []
            with station_path.open(newline="", encoding="utf-8-sig") as handle:
                for index, row in enumerate(csv.DictReader(handle)):
                    sid = str(row.get("station_id", "")).strip()
                    lat, lon = _number(row.get("latitude", row.get("lat"))), _number(row.get("longitude", row.get("lon")))
                    capacity = int(_number(row.get("pile_num"), 1) or 1)
                    if not sid or lat is None or lon is None:
                        report.errors.append({"city": city_dir.name, "row": index, "message": "invalid station metadata"})
                        continue
                    station = {"station_id": f"charged:{city_dir.name}:{sid}", "city": city_dir.name.lower(), "name": f"CHARGED {city_dir.name} station {sid}", "address": city_dir.name, "operator": "CHARGED reference", "lat": lat, "lon": lon, "capacity": capacity, "power_kw": _number(row.get("avg_power")), "connectors": [], "provider": "charged", "attribution": "CHARGED reference dataset; not Karnataka operational data.", "source_payload": row}
                    stations.append(station); city_stations.append(station)
            columns, rows = _read_wide(duration_path)
            station_by_id = {item["station_id"].rsplit(":", 1)[-1]: item for item in city_stations}
            col_map = {str(value): index for index, value in enumerate(columns)}
            for timestamp, values in rows:
                try:
                    observed_at = datetime.fromisoformat(timestamp).replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
                except ValueError:
                    report.errors.append({"city": city_dir.name, "field": "time", "message": "invalid timestamp"}); continue
                for sid, station in station_by_id.items():
                    pos = col_map.get(sid)
                    duration = _number(values[pos], 0) if pos is not None and pos < len(values) else 0
                    rate = min(1.0, max(0.0, (duration or 0) / max(1.0, station["capacity"])))
                    observations.append({"station_id": station["station_id"], "observed_at": observed_at, "occupancy_rate": rate, "occupied_count": round(rate * station["capacity"]), "duration_h": duration or 0, "volume_kwh": 0.0, "electricity_price_inr": 0.0, "service_price_inr": 0.0})
            if weather_path.exists():
                with weather_path.open(newline="", encoding="utf-8-sig") as handle:
                    for row in csv.DictReader(handle):
                        try:
                            weather.append({"city": city_dir.name.lower(), "observed_at": datetime.fromisoformat(row["time"]).replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z"), "temperature_c": _number(row.get("temp"), 0), "precipitation_mm": _number(row.get("precip"), 0), "humidity_pct": _number(row.get("humidity"), 0), "source": "charged"})
                        except (KeyError, ValueError):
                            report.errors.append({"city": city_dir.name, "field": "weather.time", "message": "invalid timestamp"})
        report.available = bool(stations)
        report.rows_received = len(stations) + len(observations)
        report.rows_accepted = len(stations) + len(observations) + len(weather)
        report.checks = {"city_directories": sorted(p.name for p in base.iterdir() if p.is_dir()), "wide_duration_tables": True, "coordinate_validation": True}
        report.warnings.append("CHARGED duration is converted to a capacity-normalized occupancy proxy; CHARGED is reference data, not Karnataka operational data.")
        return CanonicalDataset(self.source, self.source, stations, observations, weather, [], _adjacency(stations), _splits(observations), report, {"raw_format": "city folders + wide duration/weather tables", "input_root": str(self.root), "missing_field_policy": {"connectors": "empty_when_not_provided", "prices": "preserve_zero_and_report_missing", "compressed_city_files": "excluded_until_expanded"}, "per_city_reports": {city: {"stations": sum(1 for item in stations if item["city"] == city)} for city in sorted({item["city"] for item in stations})}})

    def write_streaming(self, output_root: str | Path) -> Path:
        """Write the full CHARGED dataset without materialising observations."""
        base = self.root / "CHARGED" if (self.root / "CHARGED").exists() else self.root
        target = Path(output_root) / self.source
        target.mkdir(parents=True, exist_ok=True)
        for name in ("stations.json", "observations.jsonl", "weather.jsonl", "prices.jsonl", "adjacency.json", "splits.json", "metadata.json", "quality_report.json"):
            path = target / name
            if path.exists():
                path.unlink()
        stations, timestamps, report = [], set(), QualityReport(self.source)
        with (target / "observations.jsonl").open("w", encoding="utf-8") as observation_out, (target / "weather.jsonl").open("w", encoding="utf-8") as weather_out:
            for city_dir in sorted(p for p in base.iterdir() if p.is_dir() and not p.name.endswith("_remove_zero")):
                station_path, duration_path, weather_path = city_dir / "stations.csv", city_dir / "duration.csv", city_dir / "weather.csv"
                if not station_path.exists() or not duration_path.exists():
                    report.warnings.append(f"{city_dir.name}: stations.csv or duration.csv missing")
                    continue
                city_stations = []
                with station_path.open(newline="", encoding="utf-8-sig") as handle:
                    for index, row in enumerate(csv.DictReader(handle)):
                        sid = str(row.get("station_id", "")).strip(); lat = _number(row.get("latitude", row.get("lat"))); lon = _number(row.get("longitude", row.get("lon"))); capacity = int(_number(row.get("pile_num"), 1) or 1)
                        if not sid or lat is None or lon is None:
                            report.errors.append({"city": city_dir.name, "row": index, "message": "invalid station metadata"}); continue
                        station = {"station_id": f"charged:{city_dir.name}:{sid}", "city": city_dir.name.lower(), "name": f"CHARGED {city_dir.name} station {sid}", "address": city_dir.name, "operator": "CHARGED reference", "lat": lat, "lon": lon, "capacity": capacity, "power_kw": _number(row.get("avg_power")), "connectors": [], "provider": "charged", "attribution": "CHARGED reference dataset; not Karnataka operational data.", "source_payload": row}
                        stations.append(station); city_stations.append(station)
                columns, rows = _read_wide(duration_path); col_map = {str(value): index for index, value in enumerate(columns)}; station_by_id = {item["station_id"].rsplit(":", 1)[-1]: item for item in city_stations}
                for timestamp, values in rows:
                    try:
                        observed_at = datetime.fromisoformat(timestamp).replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
                    except ValueError:
                        report.errors.append({"city": city_dir.name, "field": "time", "message": "invalid timestamp"}); continue
                    timestamps.add(observed_at)
                    for sid, station in station_by_id.items():
                        pos = col_map.get(sid); duration = _number(values[pos], 0) if pos is not None and pos < len(values) else 0; rate = min(1.0, max(0.0, (duration or 0) / max(1.0, station["capacity"])))
                        observation_out.write(json.dumps({"station_id": station["station_id"], "observed_at": observed_at, "occupancy_rate": rate, "occupied_count": round(rate * station["capacity"]), "duration_h": duration or 0, "volume_kwh": 0.0, "electricity_price_inr": 0.0, "service_price_inr": 0.0}, sort_keys=True) + "\n")
                        report.rows_accepted += 1
                if weather_path.exists():
                    with weather_path.open(newline="", encoding="utf-8-sig") as handle:
                        for row in csv.DictReader(handle):
                            try:
                                weather_out.write(json.dumps({"city": city_dir.name.lower(), "observed_at": datetime.fromisoformat(row["time"]).replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z"), "temperature_c": _number(row.get("temp"), 0), "precipitation_mm": _number(row.get("precip"), 0), "humidity_pct": _number(row.get("humidity"), 0), "source": "charged"}, sort_keys=True) + "\n")
                            except (KeyError, ValueError):
                                report.errors.append({"city": city_dir.name, "field": "weather.time", "message": "invalid timestamp"})
        report.available = bool(stations); report.rows_received = len(stations) + report.rows_accepted; report.checks = {"city_directories": sorted(p.name for p in base.iterdir() if p.is_dir()), "wide_duration_tables": True, "coordinate_validation": True, "streaming": True}; report.warnings.append("CHARGED duration is converted to a capacity-normalized occupancy proxy; CHARGED is reference data, not Karnataka operational data.")
        split_times = sorted(timestamps); n = len(split_times); train_end, val_end = max(1, int(n * .7)), max(1, int(n * .85)); splits = {"method": "chronological_unique_timestamps", "train": [split_times[0], split_times[train_end - 1]] if split_times else [], "validation": [split_times[train_end], split_times[val_end - 1]] if val_end > train_end else [], "test": [split_times[val_end], split_times[-1]] if val_end < n else [], "hash": _sha(split_times)}
        # Avoid an O(n²) all-pairs calculation for the wide CHARGED catalogue.
        # The canonical adjacency is intentionally bounded to three deterministic
        # same-city neighbours per station; full distances remain in source files.
        grouped: dict[str, list[dict]] = defaultdict(list)
        for station in stations:
            grouped[station["city"]].append(station)
        adjacency = []
        for city_stations in grouped.values():
            for index, station in enumerate(city_stations):
                for neighbour in city_stations[index + 1:index + 4]:
                    adjacency.append({"station_id": station["station_id"], "neighbour_station_id": neighbour["station_id"], "distance_km": None})
        (target / "stations.json").write_text(json.dumps(stations, indent=2, sort_keys=True), encoding="utf-8"); (target / "prices.jsonl").write_text("", encoding="utf-8"); (target / "adjacency.json").write_text(json.dumps(adjacency, indent=2, sort_keys=True), encoding="utf-8"); (target / "splits.json").write_text(json.dumps(splits, indent=2, sort_keys=True), encoding="utf-8")
        per_city = {city: {"stations": sum(1 for item in stations if item["city"] == city)} for city in sorted({item["city"] for item in stations})}
        metadata = {"source": self.source, "data_source": self.source, "content_sha256": _sha({"stations": stations, "splits": splits, "observation_count": report.rows_accepted}), "source_metadata": {"raw_format": "city folders + wide duration/weather tables", "input_root": str(self.root), "missing_field_policy": {"connectors": "empty_when_not_provided", "prices": "preserve_zero_and_report_missing", "compressed_city_files": "excluded_until_expanded"}, "per_city_reports": per_city}, "row_counts": {"stations": len(stations), "observations": report.rows_accepted, "weather": sum(1 for _ in (target / "weather.jsonl").open(encoding="utf-8")), "prices": 0, "adjacency": len(adjacency)}}
        (target / "metadata.json").write_text(json.dumps(metadata, indent=2, sort_keys=True), encoding="utf-8"); quality = asdict(report); quality["per_city"] = per_city; quality["missing_field_policy"] = metadata["source_metadata"]["missing_field_policy"]; (target / "quality_report.json").write_text(json.dumps(quality, indent=2, sort_keys=True), encoding="utf-8")
        return target
