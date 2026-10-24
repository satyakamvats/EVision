"""Seed the local four-city demo catalogue when an OCM snapshot is unavailable.

All records are explicitly synthetic. This supports an offline classroom demo and
is replaced by ``load_catalogue.py`` after a reviewed OCM import is available.
"""
import hashlib
from sqlalchemy import select
from geoalchemy2.elements import WKTElement
from app.database import session_factory
from app.models import City, IngestionRun, Station, StationConnector
from app.replay import CITIES, ReplayStore
from app.settings import get_settings


def main() -> None:
    source_hash = hashlib.sha256(f"synthetic-catalogue:{get_settings().seed}".encode()).hexdigest()
    session = session_factory()()
    try:
        if session.scalar(select(IngestionRun).where(IngestionRun.source_hash == source_hash)):
            print("synthetic catalogue already loaded")
            return
        city_ids: dict[str, object] = {}
        for city in CITIES:
            row = session.scalar(select(City).where(City.slug == city.slug))
            if row is None:
                row = City(slug=city.slug, name=city.name, aliases={"aliases": [city.name]}, boundary_geojson={"type": "Point", "coordinates": [city.lng, city.lat]})
                session.add(row)
                session.flush()
            city_ids[city.slug] = row.id
        for item in ReplayStore(get_settings().seed).stations:
            if session.scalar(select(Station).where(Station.external_id == item["id"])):
                continue
            station = Station(city_id=city_ids[item["city"]], external_id=item["id"], name=item["name"], address=item["address"], operator="EVision Synthetic Demo", capacity=item["capacity"], rated_power_kw=item["power_kw"], location=WKTElement(f"POINT({item['lng']} {item['lat']})", srid=4326), provider="EVision synthetic catalogue", attribution="Synthetic location and operational data for the EVision four-city college prototype.", source_payload={"synthetic": True})
            session.add(station)
            session.flush()
            for connector in item["connectors"]:
                session.add(StationConnector(station_id=station.id, connector_type=connector, quantity=1, power_kw=item["power_kw"]))
        session.add(IngestionRun(kind="catalogue", source="EVision synthetic demo", source_hash=source_hash, metadata_json={"cities": [city.slug for city in CITIES], "station_count": len(ReplayStore(get_settings().seed).stations)}))
        session.commit()
        print("loaded synthetic four-city catalogue")
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


if __name__ == "__main__":
    main()
