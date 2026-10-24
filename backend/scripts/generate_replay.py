"""Generate deterministic hourly replay observations and forecast bands.

The generator is intentionally one-time and seed-based: it never contacts an
external operational provider and creates reproducible synthetic artefacts.
"""
import argparse
import hashlib
from datetime import datetime, timedelta, timezone
from sqlalchemy import insert, select
from app.database import session_factory
from app.models import City, Forecast, IngestionRun, Station, StationObservation
from app.replay import ReplayStore
from app.settings import get_settings


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--months", type=int, default=6)
    parser.add_argument("--seed", type=int, default=get_settings().seed)
    args = parser.parse_args()
    source_hash = hashlib.sha256(f"replay:{args.seed}:{args.months}".encode()).hexdigest()
    session = session_factory()()
    try:
        if session.scalar(select(IngestionRun).where(IngestionRun.source_hash == source_hash)):
            raise SystemExit("This deterministic replay has already been generated.")
        existing_station_ids = set(session.scalars(select(StationObservation.station_id).distinct()))
        stations = [station for station in session.scalars(select(Station)) if station.id not in existing_station_ids]
        if not stations:
            raise SystemExit("No unseeded catalogue stations found.")
        replay = ReplayStore(args.seed)
        cities_by_id = {city.id: city.slug for city in session.scalars(select(City))}
        end = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
        start = end - timedelta(days=args.months * 30)
        observations: list[dict] = []
        forecasts: list[dict] = []

        def flush_batch() -> None:
            if observations:
                session.execute(insert(StationObservation), observations)
                observations.clear()
            if forecasts:
                session.execute(insert(Forecast), forecasts)
                forecasts.clear()
            session.flush()
        for station in stations:
            city = cities_by_id[station.city_id]
            template = {"id": str(station.id), "city": city, "capacity": station.capacity, "price": 16.0}
            at = start
            while at <= end:
                occupancy = replay.occupancy(template, at)
                occupied = round(occupancy * station.capacity)
                observations.append({"station_id": station.id, "observed_at": at, "occupied_count": occupied, "occupancy_rate": occupancy, "duration_h": .75, "volume_kwh": round(occupied * max(station.rated_power_kw or 7.2, 7.2) * .75, 2), "electricity_price_inr": 8.0, "service_price_inr": 8.0})
                for horizon in (1, 2, 3):
                    band = replay.forecast(template, at, horizon)
                    forecasts.append({"station_id": station.id, "as_of": at, "horizon_h": horizon, "q10": band.q10, "q50": band.q50, "q90": band.q90, "model_version": replay.model_version})
                if len(observations) >= 1000:
                    flush_batch()
                at += timedelta(hours=1)
        flush_batch()
        session.add(IngestionRun(kind="synthetic_replay", source="EVision deterministic generator", source_hash=source_hash, metadata_json={"seed": args.seed, "months": args.months, "station_count": len(stations)}))
        session.commit()
        print(f"generated {args.months} months for {len(stations)} stations")
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


if __name__ == "__main__":
    main()
