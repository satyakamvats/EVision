"""Fast PostgreSQL COPY loader for large canonical JSONL artifacts."""
import argparse, json, time
from datetime import datetime
from pathlib import Path
import psycopg
from geoalchemy2 import WKTElement
from sqlalchemy import select
from app.database import session_factory
from app.models import City, IngestionRun, Station, StationConnector
from app.settings import get_settings

def dsn(): return get_settings().database_url.replace("postgresql+psycopg", "postgresql")
def dt(value): return datetime.fromisoformat(value.replace("Z", "+00:00"))

def stations(root, source):
    session=session_factory()(); station_map={}; city_map={}
    try:
        rows=json.loads((root/'stations.json').read_text(encoding='utf-8'))
        for row in rows:
            slug=row['city'][:64]; city_id=city_map.get(slug)
            city=session.scalar(select(City).where(City.slug==slug)) if city_id is None else None
            if city is None and city_id is None: city=City(slug=slug,name=slug.replace('_',' ').title(),aliases={},boundary_geojson={}); session.add(city); session.flush()
            city_id = city.id if city is not None else city_id
            city_map[slug]=city_id
            station=session.scalar(select(Station).where(Station.external_id==row['station_id']))
            if station is None:
                station=Station(city_id=city_id,external_id=row['station_id'],name=row['name'],address=row.get('address'),operator=row.get('operator'),capacity=row['capacity'],rated_power_kw=row.get('power_kw'),location=WKTElement(f"POINT({row['lon']} {row['lat']})",srid=4326),provider=source,attribution=row.get('attribution',source),source_payload=row.get('source_payload') or {})
                session.add(station); session.flush()
                for connector in row.get('connectors') or []: session.add(StationConnector(station_id=station.id,connector_type=str(connector),quantity=1,power_kw=row.get('power_kw')))
            station_map[row['station_id']]=str(station.id)
        session.commit(); return station_map,city_map,len(rows)
    finally: session.close()

def load_observations(conn, root, station_map, limit, progress):
    conn.execute('CREATE TEMP TABLE ev_obs (station_id uuid, observed_at timestamptz, occupied_count integer, occupancy_rate double precision, duration_h double precision, volume_kwh double precision, electricity_price_inr double precision, service_price_inr double precision) ON COMMIT DROP')
    count=0; start=time.monotonic()
    with (root/'observations.jsonl').open(encoding='utf-8') as handle, conn.cursor().copy('COPY ev_obs FROM STDIN') as copy:
        for line in handle:
            row=json.loads(line); sid=station_map.get(row.get('station_id'))
            if not sid: continue
            copy.write_row((sid,dt(row['observed_at']),int(row.get('occupied_count',0)),float(row.get('occupancy_rate',0)),float(row.get('duration_h',0)),float(row.get('volume_kwh',0)),float(row.get('electricity_price_inr',0)),float(row.get('service_price_inr',0))))
            count+=1
            if progress and count%progress==0: print(f'observations staged: {count:,} ({count/max(.001,time.monotonic()-start):,.0f}/s)',flush=True)
            if limit and count>=limit: break
    conn.execute('INSERT INTO station_observations (id,station_id,observed_at,occupied_count,occupancy_rate,duration_h,volume_kwh,electricity_price_inr,service_price_inr) SELECT gen_random_uuid(),station_id,observed_at,occupied_count,occupancy_rate,duration_h,volume_kwh,electricity_price_inr,service_price_inr FROM ev_obs ON CONFLICT (station_id,observed_at) DO NOTHING')
    return count

def main():
    p=argparse.ArgumentParser(); p.add_argument('--source',choices=('urban_ev','charged'),required=True); p.add_argument('--artifacts',default='artifacts/datasets'); p.add_argument('--limit',type=int); p.add_argument('--progress',type=int,default=100000); args=p.parse_args()
    root=Path(args.artifacts)/args.source; metadata=json.loads((root/'metadata.json').read_text(encoding='utf-8')); station_map,city_map,n=stations(root,args.source)
    with psycopg.connect(dsn()) as conn:
        conn.execute('SET synchronous_commit=off'); count=load_observations(conn,root,station_map,args.limit,args.progress)
        if not args.limit: conn.execute("INSERT INTO ingestion_runs (id,kind,source,source_hash,metadata_json) VALUES (gen_random_uuid(),'historical_dataset',%s,%s,%s) ON CONFLICT (source_hash) DO NOTHING",(args.source,metadata['content_sha256'],json.dumps({'stations':n,'observations':count,'loader':'postgres_copy'})))
        conn.commit()
    print(f'loaded {args.source}: {n:,} stations, {count:,} observations')
if __name__=='__main__': main()
