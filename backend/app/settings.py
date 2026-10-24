from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="EVISION_", extra="ignore")
    database_url: str = "postgresql+psycopg://evision:evision@localhost:5433/evision"
    ocm_api_key: str | None = None
    electricity_maps_api_key: str | None = None
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    seed: int = 20261024
    mode: str = "synthetic_replay"

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
