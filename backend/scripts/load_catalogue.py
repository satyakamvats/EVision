"""Load a reviewed Open Charge Map snapshot into PostgreSQL/PostGIS.

Run Alembic migrations first. Existing external station identifiers are skipped,
which keeps catalogue imports idempotent and preserves an ingestion audit trail.
"""
import argparse
import hashlib
import json
from pathlib import Path
from sqlalchemy import select
from geoalchemy2.elements import WKTElement
from app.database import session_factory
from app.models import City, IngestionRun, Station, StationConnector
from app.replay import CITIES


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("snapshot", type=Path)
    args = parser.parse_args()
    raw = args.snapshot.read_bytes()
    source_hash = hashlib.sha256(raw).hexdigest()
    payload = json.loads(raw)
    session = session_factory()()
    try:
        if session.scalar(select(IngestionRun).where(IngestionRun.source_hash == source_hash)):
            raise SystemExit("This snapshot has already been loaded.")
        city_rows: dict[str, City] = {}
        for city in CITIES:
            row = session.scalar(select(City).where(City.slug == city.slug))
            if row is None:
                row = City(slug=city.slug, name=city.name, aliases={"aliases": [city.name]}, boundary_geojson={"type": "Point", "coordinates": [city.lng, city.lat]})
                session.add(row)
                session.flush()
            city_rows[city.slug] = row
        added = 0
        for record in payload.get("records", []):
            address = record.get("AddressInfo") or {}
            external_id = f"ocm-{record.get('ID')}"
            if record.get("evision_city") not in city_rows or not record.get("ID"):
                continue
            if session.scalar(select(Station).where(Station.external_id == external_id)):
                continue
            lat, lon = address.get("Latitude"), address.get("Longitude")
            if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
                continue
            connections = record.get("Connections") or []
            capacity = int(record.get("NumberOfPoints") or len(connections) or 1)
            provider = (record.get("DataProvider") or {}).get("Title") or "Open Charge Map"
            station = Station(city_id=city_rows[record["evision_city"]].id, external_id=external_id, name=address.get("Title") or external_id, address=address.get("AddressLine1") or address.get("Town"), operator=(record.get("OperatorInfo") or {}).get("Title"), capacity=max(1, capacity), rated_power_kw=max([(item.get("PowerKW") or 0) for item in connections], default=None) or None, location=WKTElement(f"POINT({lon} {lat})", srid=4326), provider=provider, attribution=f"Location metadata provided by {provider} via Open Charge Map; verify provider licence before publication.", source_payload=record)
            session.add(station)
            session.flush()
            for connection in connections:
                label = (connection.get("ConnectionType") or {}).get("Title") or "Unknown"
                session.add(StationConnector(station_id=station.id, connector_type=label, quantity=1, power_kw=connection.get("PowerKW")))
            added += 1
        session.add(IngestionRun(kind="catalogue", source="Open Charge Map", source_hash=source_hash, metadata_json={"records_received": len(payload.get("records", [])), "stations_added": added}))
        session.commit()
        print(f"loaded {added} stations")
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


if __name__ == "__main__":
    main()
