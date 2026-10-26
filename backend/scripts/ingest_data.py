"""Build a canonical Phase 4 dataset artifact.

Examples:
  python -m scripts.ingest_data --source synthetic --output artifacts/datasets
  python -m scripts.ingest_data --source urban_ev --input data/raw/urban_ev --output artifacts/datasets
  python -m scripts.ingest_data --source ocm --input data/raw/ocm-karnataka.json --output artifacts/datasets
"""
import argparse
from app.data_layer import FileDatasetAdapter, OpenChargeMapAdapter, SyntheticAdapter, write_dataset
from app.research_adapters import ChargedAdapter, UrbanEVAdapter


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", choices=("synthetic", "urban_ev", "charged", "ocm"), required=True)
    parser.add_argument("--input")
    parser.add_argument("--output", default="artifacts/datasets")
    parser.add_argument("--seed", type=int, default=20261024)
    parser.add_argument("--hours", type=int, default=168)
    args = parser.parse_args()
    if args.source == "synthetic":
        dataset = SyntheticAdapter(args.seed, args.hours).build()
    elif args.source == "ocm":
        if not args.input:
            raise SystemExit("--input is required for --source ocm")
        dataset = OpenChargeMapAdapter(args.input).build()
    elif args.source == "urban_ev":
        dataset = UrbanEVAdapter(args.input).build()
    elif args.source == "charged":
        target = ChargedAdapter(args.input).write_streaming(args.output)
        print(f"charged streaming artifact -> {target}")
        return
    else:
        dataset = FileDatasetAdapter(args.source, args.input).build()
    target = write_dataset(dataset, args.output)
    print(f"{dataset.source}: {len(dataset.stations)} stations, {len(dataset.observations)} observations -> {target}")
    if dataset.quality and dataset.quality.errors:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
