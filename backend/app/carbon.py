from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache

import httpx


@dataclass(frozen=True)
class CarbonResult:
    intensity_g_per_kwh: float
    source: str
    scenario: str | None = None


class CarbonProvider:
    @lru_cache(maxsize=256)
    def intensity(self, at: datetime, lat: float, lon: float, scenario: str) -> CarbonResult:
        raise NotImplementedError


class ScenarioCarbonProvider(CarbonProvider):
    profiles = {"flat": 550.0, "low": 350.0, "high": 750.0}

    def intensity(self, at: datetime, lat: float, lon: float, scenario: str) -> CarbonResult:
        if scenario == "tou_profile":
            value = 650.0 if 17 <= at.hour <= 21 else 480.0
        else:
            value = self.profiles.get(scenario, self.profiles["flat"])
        return CarbonResult(value, "scenario", scenario)


class ElectricityMapsCarbonProvider(CarbonProvider):
    def __init__(self, token: str | None, fallback: CarbonProvider | None = None):
        self.token = token
        self.fallback = fallback or ScenarioCarbonProvider()

    @lru_cache(maxsize=256)
    def intensity(self, at: datetime, lat: float, lon: float, scenario: str) -> CarbonResult:
        if not self.token:
            return self.fallback.intensity(at, lat, lon, scenario)
        try:
            response = httpx.get("https://api.electricitymaps.com/v4/carbon-intensity/latest", params={"lat": lat, "lon": lon}, headers={"auth-token": self.token}, timeout=5)
            response.raise_for_status()
            value = response.json().get("carbonIntensity")
            if isinstance(value, (int, float)):
                return CarbonResult(float(value), "observed")
        except (httpx.HTTPError, ValueError):
            pass
        return self.fallback.intensity(at, lat, lon, scenario)
