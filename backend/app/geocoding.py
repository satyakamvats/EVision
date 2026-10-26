from dataclasses import dataclass
import time
from functools import lru_cache
import httpx


@dataclass(frozen=True)
class GeocodeResult:
    display_name: str
    lat: float
    lon: float


class NominatimGeocoder:
    def __init__(self, base_url: str, user_agent: str, min_interval_s: float = 1.0):
        self.base_url = base_url.rstrip("/")
        self.user_agent = user_agent
        self.min_interval_s = min_interval_s
        self._last_request = 0.0

    @lru_cache(maxsize=256)
    def search(self, query: str, limit: int = 5) -> tuple[GeocodeResult, ...]:
        elapsed = time.monotonic() - self._last_request
        if elapsed < self.min_interval_s:
            time.sleep(self.min_interval_s - elapsed)
        response = httpx.get(f"{self.base_url}/search", params={"q": query, "format": "jsonv2", "limit": limit}, headers={"User-Agent": self.user_agent}, timeout=5)
        self._last_request = time.monotonic()
        response.raise_for_status()
        return tuple(GeocodeResult(item["display_name"], float(item["lat"]), float(item["lon"])) for item in response.json())
