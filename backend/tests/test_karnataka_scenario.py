from pathlib import Path

from app.karnataka_scenario import build_karnataka_scenario, discover_reference_profiles


def test_reference_profiles_are_discovered():
    urban, charged, sources = discover_reference_profiles(Path("data/raw"))
    assert len(urban) == 24
    assert "urban_ev" in sources
    assert charged


def test_karnataka_scenario_is_explicitly_not_observed():
    dataset = build_karnataka_scenario("data/raw/ocm-karnataka.json", "data/raw", days=1)
    assert dataset.data_source == "scenario_estimate"
    assert dataset.quality.checks["reference_only"] is True
    assert len(dataset.observations) == len(dataset.stations) * 24
    assert "not Karnataka observed" in dataset.quality.warnings[0]
