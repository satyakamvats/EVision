from app.research_adapters import ChargedAdapter, UrbanEVAdapter


def test_urbanev_real_layout_is_parsed():
    dataset = UrbanEVAdapter("data/raw/urban_ev").build()
    assert dataset.quality.available
    assert len(dataset.stations) >= 1600
    assert dataset.quality.checks["hourly_source"] is True
    assert dataset.observations


def test_charged_real_layout_is_parsed():
    dataset = ChargedAdapter("data/raw/charged").build()
    assert dataset.quality.available
    assert len(dataset.stations) > 0
    assert dataset.quality.checks["wide_duration_tables"] is True
    assert dataset.observations
