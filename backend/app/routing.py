from dataclasses import dataclass
import time
from math import asin, cos, pi, sin, sqrt

import httpx


@dataclass(frozen=True)
class RouteResult:
    distance_km: float
    duration_min: float
    source: str
    geometry: list[list[float]] | None = None


def haversine_km(a_lat: float, a_lon: float, b_lat: float, b_lon: float) -> float:
    radius = 6371.0
    dlat = pi / 180 * (b_lat - a_lat)
    dlon = pi / 180 * (b_lon - a_lon)
    value = sin(dlat / 2) ** 2 + cos(a_lat * pi / 180) * cos(b_lat * pi / 180) * sin(dlon / 2) ** 2
    return 2 * radius * asin(sqrt(max(0.0, min(1.0, value))))


class RoutingProvider:
    def route(self, origin: tuple[float, float], destination: tuple[float, float]) -> RouteResult:
        raise NotImplementedError


class FallbackRoutingProvider(RoutingProvider):
    def __init__(self, base: RoutingProvider | None = None, speed_kmh: float = 30.0, detour_factor: float = 1.15):
        self.base = base
        self.speed_kmh = speed_kmh
        self.detour_factor = detour_factor

    def route(self, origin: tuple[float, float], destination: tuple[float, float]) -> RouteResult:
        if self.base:
            try:
                return self.base.route(origin, destination)
            except (httpx.HTTPError, TimeoutError, ValueError):
                pass
        distance = haversine_km(*origin, *destination) * self.detour_factor
        return RouteResult(round(distance, 3), round(distance / self.speed_kmh * 60, 1), "haversine_fallback")


class OsrmRoutingProvider(RoutingProvider):
    def __init__(self, base_url: str, timeout_s: float = 4.0, cache_ttl_s: int = 3600):
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s
        self.cache_ttl_s = cache_ttl_s
        self._cache: dict[tuple[float, float, float, float], tuple[float, RouteResult]] = {}

    def _route(self, origin_lat: float, origin_lon: float, destination_lat: float, destination_lon: float) -> RouteResult:
        key = (origin_lat, origin_lon, destination_lat, destination_lon)
        cached = self._cache.get(key)
        if cached and time.monotonic() - cached[0] < self.cache_ttl_s:
            return cached[1]
        url = f"{self.base_url}/{origin_lon},{origin_lat};{destination_lon},{destination_lat}"
        response = httpx.get(url, params={"overview": "full", "geometries": "geojson", "steps": "false"}, timeout=self.timeout_s)
        response.raise_for_status()
        payload = response.json()
        route = (payload.get("routes") or [])[0]
        coordinates = route.get("geometry", {}).get("coordinates", [])
        geometry = [[float(lat), float(lon)] for lon, lat in coordinates] or None
        result = RouteResult(round(route["distance"] / 1000, 3), round(route["duration"] / 60, 1), "osrm", geometry)
        self._cache[key] = (time.monotonic(), result)
        return result

    def route(self, origin: tuple[float, float], destination: tuple[float, float]) -> RouteResult:
        return self._route(round(origin[0], 5), round(origin[1], 5), round(destination[0], 5), round(destination[1], 5))
