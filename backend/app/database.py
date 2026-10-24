from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from .settings import get_settings


def session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=create_engine(get_settings().database_url), expire_on_commit=False)
