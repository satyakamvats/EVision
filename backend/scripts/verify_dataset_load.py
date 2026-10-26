from sqlalchemy import text
from app.database import session_factory

s = session_factory()()
print('stations', s.execute(text('select provider,count(*) from stations group by provider order by provider')).all())
print('observations', s.execute(text('select count(*) from station_observations')).scalar())
print('weather', s.execute(text('select count(*) from weather_observations')).scalar())
print('ingestion', s.execute(text("select source, metadata_json->>'observations' from ingestion_runs where kind='historical_dataset' order by source")).all())
s.close()
