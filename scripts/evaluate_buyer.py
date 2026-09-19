"""Offline evaluation: python scripts/evaluate_buyer.py evidence.json [--persist].

Input: {"records": [...], "protocol": {...}, "params": {...}}.
Never reconstruct vintage_verified from today's revised history. --persist writes
the current configured database, so use a test DB unless intentionally promoting.
"""
import argparse
import json
from pathlib import Path

from realty_signal.brain.evaluation import Protocol, evaluate, save_result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--persist", action="store_true")
    args = parser.parse_args()
    data = json.loads(args.input.read_text())
    result = evaluate(data["records"], Protocol(**data["protocol"]), params=data["params"])
    if args.persist:
        result = {**result, "validation_id": save_result(result)}
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
