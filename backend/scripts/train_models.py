import argparse

from app.ml_training import train_production_models


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifacts", default="artifacts")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--scenario-root", default="artifacts/datasets/karnataka_scenario")
    parser.add_argument("--max-rows", type=int, default=180000)
    args = parser.parse_args()
    result = train_production_models(args.artifacts, args.scenario_root, args.seed, args.max_rows)
    print(f"trained {len(result['models'])} horizon models")


if __name__ == "__main__":
    main()
