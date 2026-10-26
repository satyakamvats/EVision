"""Stream a canonical dataset artifact into the application database."""
import argparse
import hashlib
import json
from pathlib import Path
from sqlalchemy import select
from geoalchemy2 import WKTElement
from app.database import session_factory
from app.models import City, IngestionRun, Station, StationConnector, StationObservation, WeatherObservation


def chunks(handle, size=1000):
    batch = []
    for line in handle:
        if line.strip():
            batch.append(json.loads(line))
            if len(batch) >= size:
                yield batch; batch = []
    if batch:
        yield batch


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", choices=("urban_ev", "charged"), required=True)
    parser.add_argument("--artifacts", default="artifacts/datasets")
    parser.add_argument("--batch-size", type=int, default=1000)
    args = parser.parse_args()
    root = Path(args.artifacts) / args.source
    metadata = json.loads((root / "metadata.json").read_text(encoding="utf-8"))
    session = session_factory()()
    try:
        existing = session.scalar(select(IngestionRun).where(IngestionRun.source_hash == metadata["content_sha256"]))
        if existing:
            print(f"{args.source} artifact already loaded")
            return
        station_map = {}
        stations = json.loads((root / "stations.json").read_text(encoding="utf-8"))
        city_map = {}
        for row in stations:
            city_slug = row["city"][:64]
            city = city_map.get(city_slug) or session.scalar(select(City).where(City.slug == city_slug))
            if city is None:
                city = City(slug=city_slug, name=city_slug.replace("_", " ").title(), aliases={}, boundary_geojson={}); session.add(city); session.flush()
            city_map[city_slug] = city
            station = session.scalar(select(Station).where(Station.external_id == row["station_id"]))
            if station is None:
                station = Station(city_id=city.id, external_id=row["station_id"], name=row["name"], address=row.get("address"), operator=row.get("operator"), capacity=row["capacity"], rated_power_kw=row.get("power_kw"), location=WKTElement(f"POINT({row['lon']} {row['lat']})", srid=4326), provider=args.source, attribution=row.get("attribution", args.source), source_payload=row.get("source_payload") or {})
                session.add(station); session.flush()
                for connector in row.get("connectors") or []:
                    session.add(StationConnector(station_id=station.id, connector_type=str(connector), quantity=1, power_kw=row.get("power_kw")))
            station_map[row["station_id"]] = station.id
        session.commit()
        observation_count = 0
        with (root / "observations.jsonl").open(encoding="utf-8") as handle:
            for batch in chunks(handle, args.batch_size):
                for row in batch:
                    station_id = station_map.get(row["station_id"])
                    if station_id:
                        from datetime import datetime
                        observed_at = datetime.fromisoformat(row["observed_at"].replace("Z", "+00:00"))
                        session.add(StationObservation(station_id=station_id, observed_at=observed_at, occupied_count=row.get("occupied_count", 0), occupancy_rate=row.get("occupancy_rate", 0), duration_h=row.get("duration_h", 0), volume_kwh=row.get("volume_kwh", 0), electricity_price_inr=row.get("electricity_price_inr", 0), service_price_inr=row.get("service_price_inr", 0)))
                        observation_count += 1
                session.commit()
        weather_count = 0
        weather_path = root / "weather.jsonl"
        if weather_path.exists():
            with weather_path.open(encoding="utf-8") as handle:
                for batch in chunks(handle, args.batch_size):
                    for row in batch:
                        city = city_map.get(row["city"])
                        if city:
                            from datetime import datetime
                            session.add(WeatherObservation(city_id=city.id, observed_at=datetime.fromisoformat(row["observed_at"].replace("Z", "+00:00")), temperature_c=row.get("temperature_c", 0), precipitation_mm=row.get("precipitation_mm", 0), humidity_pct=row.get("humidity_pct", 0), source=args.source)); weather_count += 1
                    session.commit()
        session.add(IngestionRun(kind="historical_dataset", source=args.source, source_hash=metadata["content_sha256"], metadata_json={"stations": len(stations), "observations": observation_count, "weather": weather_count, "artifact": str(root)})); session.commit()
        print(f"loaded {args.source}: {len(stations)} stations, {observation_count} observations, {weather_count} weather rows")
    finally:
        session.close()


if __name__ == "__main__":
    main()
