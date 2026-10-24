"""One-time Open Charge Map importer.

Requires EVISION_OCM_API_KEY. Results should be reviewed for provider-specific
licence/attribution before becoming a production catalogue.
"""
import argparse
import hashlib
import json
from pathlib import Path
import httpx
from app.settings import get_settings

# Deliberately conservative bounding boxes for the four-city college pilot.
# Replace these with approved administrative GeoJSON before a public deployment.
CITIES = {
    "bengaluru": {"centre": (12.9716, 77.5946), "bbox": (12.72, 77.35, 13.20, 77.85)},
    "mysuru": {"centre": (12.2958, 76.6394), "bbox": (12.15, 76.50, 12.42, 76.78)},
    "hubballi-dharwad": {"centre": (15.3647, 75.1240), "bbox": (15.23, 74.95, 15.52, 75.25)},
    "belagavi": {"centre": (15.8497, 74.4977), "bbox": (15.70, 74.34, 16.00, 74.68)},
}


def in_city(record: dict, bbox: tuple[float, float, float, float]) -> bool:
    address = record.get("AddressInfo") or {}
    lat, lon = address.get("Latitude"), address.get("Longitude")
    south, west, north, east = bbox
    return isinstance(lat, (int, float)) and isinstance(lon, (int, float)) and south <= lat <= north and west <= lon <= east


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("data/raw/ocm-karnataka.json"))
    parser.add_argument("--max-results", type=int, default=100)
    args = parser.parse_args()
    key = get_settings().ocm_api_key
    if not key:
        raise SystemExit("EVISION_OCM_API_KEY is required; no request was made.")
    records = []
    with httpx.Client(timeout=30, headers={"X-API-Key": key, "User-Agent": "EVision college prototype"}) as client:
        for city, definition in CITIES.items():
            lat, lon = definition["centre"]
            response = client.get("https://api.openchargemap.io/v3/poi/", params={"output": "json", "countrycode": "IN", "latitude": lat, "longitude": lon, "distance": 60, "distanceunit": "KM", "maxresults": args.max_results, "compact": "true", "verbose": "false"})
            response.raise_for_status()
            for record in response.json():
                if in_city(record, definition["bbox"]):
                    record["evision_city"] = city
                    records.append(record)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    payload = {"source": "Open Charge Map", "records": records, "sha256": hashlib.sha256(json.dumps(records, sort_keys=True).encode()).hexdigest()}
    args.output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"wrote {len(records)} records to {args.output}")


if __name__ == "__main__":
    main()
