"""Load bounded canonical weather JSONL rows using PostgreSQL COPY."""
import argparse, json
from datetime import datetime
from pathlib import Path
import psycopg
from app.settings import get_settings

def main():
    p=argparse.ArgumentParser(); p.add_argument('--source',choices=('urban_ev','charged'),required=True); p.add_argument('--artifacts',default='artifacts/datasets'); args=p.parse_args()
    root=Path(args.artifacts)/args.source; path=root/'weather.jsonl';
    if not path.exists(): print(f'{args.source}: no weather artifact'); return
    dsn=get_settings().database_url.replace('postgresql+psycopg','postgresql'); count=0
    with psycopg.connect(dsn) as conn:
        conn.execute('CREATE TEMP TABLE ev_weather (city text, observed_at timestamptz, temperature_c double precision, precipitation_mm double precision, humidity_pct double precision, source text) ON COMMIT DROP')
        with path.open(encoding='utf-8') as handle, conn.cursor().copy('COPY ev_weather FROM STDIN') as copy:
            for line in handle:
                row=json.loads(line); copy.write_row((row['city'],datetime.fromisoformat(row['observed_at'].replace('Z','+00:00')),float(row.get('temperature_c',0)),float(row.get('precipitation_mm',0)),float(row.get('humidity_pct',0)),row.get('source',args.source))); count+=1
        conn.execute('INSERT INTO weather_observations (id,city_id,observed_at,temperature_c,precipitation_mm,humidity_pct,source) SELECT gen_random_uuid(),c.id,w.observed_at,w.temperature_c,w.precipitation_mm,w.humidity_pct,w.source FROM ev_weather w JOIN cities c ON c.slug=w.city ON CONFLICT (city_id,observed_at) DO NOTHING'); conn.commit()
    print(f'loaded {args.source}: {count:,} weather rows')
if __name__=='__main__': main()
