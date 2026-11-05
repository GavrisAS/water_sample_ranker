"""Run repeatable gates with sanitized stdout and a small aggregate receipt."""

import argparse
import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from scripts.public_gate import scan_source_archive, scan_tree
from water_sample_ranker.handoff.evidence import protected, run_gate


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--public", action="store_true")
    args = p.parse_args()
    root = Path.cwd()
    before = protected(root, {})
    directory = root / "artifacts/private"
    directory.mkdir(parents=True, exist_ok=True)
    commands = {
        "ruff": ["uv", "run", "--no-sync", "ruff", "check", "."],
        "format": ["uv", "run", "--no-sync", "ruff", "format", "--check", "."],
        "ty": ["uv", "run", "--no-sync", "ty", "check"],
        "pytest": [
            "uv",
            "run",
            "--no-sync",
            "pytest",
            "-q",
            "--junitxml=artifacts/private/pytest.xml",
        ],
        "cli": [sys.executable, "-m", "water_sample_ranker.cli", "--version"],
        "gui": [sys.executable, "-m", "water_sample_ranker", "--smoke-test"],
    }
    gates = {}
    for name, command in commands.items():
        gate, log = run_gate(command, root, 600, name)
        print(log.decode(), end="", flush=True)
        gates[name] = gate["status"]
        if gate["status"] != "PASS":
            raise SystemExit("CHECK_FAILED_" + name.upper())
    if args.public:
        scan_tree(root)
        archive = directory / "public-source.tar"
        with archive.open("wb") as stream:
            subprocess.run(["git", "archive", "HEAD"], stdout=stream, check=True)
        scan_source_archive(archive, root)
        gates["public_privacy"] = "PASS"
    if protected(root, {}) != before:
        raise SystemExit("PROTECTED_INPUTS_CHANGED")
    suite = ET.parse(directory / "pytest.xml").getroot()
    stats = {
        key: sum(int(node.get(key, "0")) for node in suite.findall("testsuite"))
        for key in ("tests", "failures", "errors", "skipped")
    }
    if not stats["tests"] or stats["failures"] or stats["errors"]:
        raise SystemExit("PYTEST_RECEIPT_INVALID")
    receipt = {
        "status": "PASS",
        "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "python": sys.version.split()[0],
        "gates": gates,
        "pytest": stats,
    }
    (directory / "checks.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print("AUTOMATED_CHECKS_PASS; NATIVE_EXCEL_AND_HUMAN_WINDOWS_UX_NOT_RUN")


if __name__ == "__main__":
    main()
