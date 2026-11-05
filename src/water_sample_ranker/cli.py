"""CLI and GUI share the same service; validate never writes an output."""

import argparse
import json
from datetime import date
from pathlib import Path

from water_sample_ranker import __version__
from water_sample_ranker.config import OPTIONAL_ROLES, ROLES, load_config
from water_sample_ranker.service import ServiceError, run_update, run_update_all, run_validate


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Калькулятор отбора проб воды — этап B; рейтинг PENDING"
    )
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("validate", "update", "update-all"):
        p = sub.add_parser(command)
        p.add_argument("--project", type=Path, default=Path.cwd())
        p.add_argument("--config", type=Path)
        p.add_argument("--as-of", type=date.fromisoformat)
        p.add_argument("--json", action="store_true")
        for role in ("calculator", *ROLES, *OPTIONAL_ROLES, "output"):
            p.add_argument("--" + role.replace("_", "-"))
        if command == "update":
            p.add_argument("--field", required=True)
        if command == "update-all":
            p.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        config = load_config(args.project, args.config)
        for role in ("calculator", *ROLES, *OPTIONAL_ROLES, "output"):
            if getattr(args, role):
                config.paths[role] = getattr(args, role)
        if args.command == "update-all":
            results = run_update_all(config, as_of=args.as_of, output_dir=args.output_dir)
            print(
                json.dumps([r.report for r in results], ensure_ascii=False, indent=2, default=str)
                if args.json
                else "\n\n".join(r.summary() for r in results)
            )
            return 0
        result = (
            run_validate(config, as_of=args.as_of)
            if args.command == "validate"
            else run_update(config, target_field=args.field, as_of=args.as_of)
        )
        print(
            json.dumps(result.report, ensure_ascii=False, indent=2, default=str)
            if args.json
            else result.summary()
        )
        return 2 if result.has_errors else 0
    except (OSError, ValueError, ServiceError) as error:
        print(str(error))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
