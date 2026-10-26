from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from .settings import get_settings


def session_factory() -> sessionmaker[Session]:
    url = get_settings().database_url
    connect_args = {"connect_timeout": 3} if url.startswith("postgresql") else {}
    return sessionmaker(bind=create_engine(url, connect_args=connect_args, pool_pre_ping=True), expire_on_commit=False)
