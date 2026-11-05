"""Exercise the real frozen CLI with generated DEMO inputs, never production data."""

import argparse
import json
import os
import subprocess
from datetime import date
from pathlib import Path

from scripts.make_synthetic_demo import generate
from water_sample_ranker.bundle import verify_bundle
from water_sample_ranker.excel.compatibility import inspect_template
from water_sample_ranker.handoff.evidence import clean_log


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--binary", type=Path, required=True)
    p.add_argument("--version", required=True)
    p.add_argument("--source-sha", required=True)
    args = p.parse_args()
    binary = args.binary.resolve()
    run = binary.parent.parent.parent
    config = generate(run / "frozen-demo")
    day = date(2026, 11, 5)
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    for index, command in enumerate(
        (
            ["--version"],
            ["--cli", "--version"],
            ["--cli", "validate", "--project", str(config.root), "--as-of", day.isoformat()],
            [
                "--cli",
                "update",
                "--project",
                str(config.root),
                "--as-of",
                day.isoformat(),
                "--field",
                "береговое",
                "--output",
                str(run / "single.xlsx"),
            ],
            [
                "--cli",
                "update-all",
                "--project",
                str(config.root),
                "--as-of",
                day.isoformat(),
                "--output-dir",
                str(run / "frozen-output"),
            ],
        )
    ):
        log = run / f"cli-{index}.log"
        if "--cli" in command:
            command = ["--cli-log", str(log), *command]
        completed = subprocess.run(
            [str(binary), *command],
            cwd=run,
            env=env,
            check=False,
            timeout=180,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if completed.returncode:
            if log.is_file():
                print(clean_log(log.read_text(encoding="utf-8")[-16384:], Path.cwd(), run))
            raise ValueError(f"FROZEN_CLI_STEP_{index}_FAILED_EXIT_{completed.returncode}")
        if index == 1 and log.read_text(encoding="utf-8").strip() != args.version:
            raise ValueError("FROZEN_CLI_VERSION_OUTPUT_MISMATCH")
    bundle = verify_bundle(
        run / "frozen-output", expected_date=day.isoformat(), expected_version=args.version
    )
    if bundle["status"] != "COMPLETE":
        raise ValueError("FROZEN_BUNDLE_INCOMPLETE")
    for path in (run / "frozen-output").glob("*.load-report.json"):
        inspect_template(path.with_suffix("").with_suffix(".xlsx"))
        report = json.loads(path.read_text(encoding="utf-8"))
        for row in report["rows"]:
            if not row["identity"]["display"].startswith("DEMO-"):
                raise ValueError("FROZEN_DEMO_IDENTITY")
            if any(
                c["normalized_score"] is not None or c["weighted_contribution"] is not None
                for c in row["criteria"]
            ):
                raise ValueError("FROZEN_SCORE_PRESENT")
    (run / "frozen-cli.json").write_text(
        json.dumps(
            {
                "status": "PASS",
                "version": args.version,
                "source_commit": args.source_sha,
                "empty_score_rank_top": True,
            }
        ),
        encoding="utf-8",
    )
    print("FROZEN_SYNTHETIC_CLI_PASS")


if __name__ == "__main__":
    main()
