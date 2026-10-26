import argparse

from app.decision import DecisionEngine
from app.evaluation import run_synthetic_replay, write_replay_artifact
from app.replay import ReplayStore


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--steps", type=int, default=48)
    parser.add_argument("--artifacts", default="artifacts")
    args = parser.parse_args()
    store = ReplayStore(args.seed)
    payload = run_synthetic_replay(store, DecisionEngine(store), args.seed, args.steps)
    target = write_replay_artifact(payload, args.artifacts)
    print(f"wrote deterministic replay metrics to {target}")


if __name__ == "__main__":
    main()
