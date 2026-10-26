"""Create four-city Karnataka scenario data from OCM + reference datasets."""
import argparse
from app.data_layer import write_dataset
from app.karnataka_scenario import build_karnataka_scenario


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ocm", default="data/raw/ocm-karnataka.json")
    parser.add_argument("--raw-root", default="data/raw")
    parser.add_argument("--output", default="artifacts/datasets")
    parser.add_argument("--days", type=int, default=180)
    args = parser.parse_args()
    dataset = build_karnataka_scenario(args.ocm, args.raw_root, days=args.days)
    target = write_dataset(dataset, args.output)
    print(f"karnataka_scenario: {len(dataset.stations)} stations, {len(dataset.observations)} observations -> {target}")


if __name__ == "__main__":
    main()
