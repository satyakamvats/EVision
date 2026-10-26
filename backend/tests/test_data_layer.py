import json
from pathlib import Path

from app.data_layer import FileDatasetAdapter, OpenChargeMapAdapter, SyntheticAdapter, write_dataset


def test_synthetic_adapter_is_deterministic(tmp_path: Path):
    first, second = SyntheticAdapter(seed=7, hours=4).build(), SyntheticAdapter(seed=7, hours=4).build()
    assert first.content_hash() == second.content_hash()
    assert len(first.stations) == 32
    assert len(first.observations) == 128
    assert first.splits["method"] == "chronological_unique_timestamps"
    assert write_dataset(first, tmp_path).joinpath("quality_report.json").exists()


def test_missing_charged_is_explicit_empty_state(tmp_path: Path):
    dataset = FileDatasetAdapter("charged", tmp_path / "does-not-exist").build()
    assert dataset.quality.available is False
    assert dataset.stations == []
    assert "not available" in dataset.quality.warnings[0]


def test_file_adapter_reports_bad_rows(tmp_path: Path):
    path = tmp_path / "urban.json"
    path.write_text(json.dumps([{"station_id": "bad", "lat": 500, "lon": 77, "capacity": 2}]), encoding="utf-8")
    dataset = FileDatasetAdapter("urban_ev", path).build()
    assert dataset.stations == []
    assert dataset.quality.errors


def test_ocm_normalizes_metadata_without_occupancy(tmp_path: Path):
    path = tmp_path / "ocm.json"
    path.write_text(json.dumps({"records": [{"ID": 1, "AddressInfo": {"Title": "A", "Latitude": 12.9, "Longitude": 77.5, "Town": "Bengaluru"}, "NumberOfPoints": 4, "Connections": [{"ConnectionType": {"Title": "CCS"}}]}]}), encoding="utf-8")
    dataset = OpenChargeMapAdapter(path).build()
    assert dataset.stations[0]["capacity"] == 4
    assert dataset.source_metadata["occupancy"] == "not_provided"
    assert dataset.stations[0]["connectors"] == ["CCS"]
