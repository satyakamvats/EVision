from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="EVISION_", extra="ignore")
    database_url: str = "postgresql+psycopg://evision:evision@localhost:5433/evision"
    ocm_api_key: str | None = None
    electricity_maps_api_key: str | None = None
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173,http://localhost:5174,http://127.0.0.1:5174"
    seed: int = 20261024
    mode: str = "synthetic_replay"
    supabase_url: str | None = None
    supabase_jwks_url: str | None = None
    supabase_anon_key: str | None = None
    routing_provider: str = "haversine"
    routing_url: str = "https://router.project-osrm.org/route/v1/driving"
    routing_timeout_s: float = 4.0
    routing_cache_ttl_s: int = 3600
    geocoding_url: str = "https://nominatim.openstreetmap.org"
    geocoding_user_agent: str = "EVision/0.2 local research application"
    geocoding_min_interval_s: float = 1.0
    artifacts_dir: str = "artifacts"
    require_auth_for_saved_data: bool = True
    recommendation_rate_limit_per_minute: int = 60
    reveal_rate_limit_per_minute: int = 30

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
