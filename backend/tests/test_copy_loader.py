from pathlib import Path
from scripts.load_dataset_copy import dt

def test_copy_loader_timestamp_parser():
    assert dt('2026-01-01T00:00:00Z').tzinfo is not None
