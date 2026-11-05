"""Изолированная opt-in проверка production; stdout не содержит строк/путей."""

import argparse
from pathlib import Path

from scripts.verify_handoff import canonical
from water_sample_ranker.handoff.evidence import production


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("root", type=Path)
    p.add_argument("output", type=Path)
    args = p.parse_args()
    try:
        result = production(args.root, True)
        args.output.write_bytes(canonical(result))
        print("PRODUCTION_AGGREGATES_ONLY")
        return 0 if result["status"] == "PASS" else 2
    except Exception:
        print("PRODUCTION_CHECK_BLOCKED")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
