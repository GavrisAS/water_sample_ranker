"""Fresh isolated onedir with one injected artifact version and source identity."""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from water_sample_ranker import SOURCE_VERSION
from water_sample_ranker.build_provenance import build_inputs, content_digest
from water_sample_ranker.source_manifest import fingerprint
from water_sample_ranker.versioning import parse_version


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    p.add_argument("--version", default=SOURCE_VERSION)
    p.add_argument("--source-sha")
    args = p.parse_args()
    parse_version(args.version)
    root = args.root.resolve()
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    if args.source_sha and args.source_sha != commit:
        raise ValueError("BUILD_SOURCE_SHA_MISMATCH")
    if subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=no"], cwd=root):
        raise ValueError("BUILD_REQUIRES_CLEAN_COMMIT")
    inputs = build_inputs(root)
    run = (
        root
        / "artifacts/private"
        / ("frozen-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8])
    )
    source = run / "source"
    source.mkdir(parents=True)
    for name in inputs:
        target = source / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / name, target)
    generated = source / "src/water_sample_ranker/_build_version.py"
    generated.write_text(
        f'RELEASE_VERSION = "{args.version}"\nSOURCE_COMMIT = "{commit}"\n', encoding="utf-8"
    )
    for name in ("pyproject.toml", "uv.lock"):
        path = source / name
        text = path.read_text(encoding="utf-8")
        if name == "pyproject.toml":
            text, count = re.subn(
                r'(?m)^version = "' + re.escape(SOURCE_VERSION) + r'"$',
                f'version = "{args.version}"',
                text,
                count=1,
            )
        else:
            text, count = re.subn(
                r'(name = "water-sample-ranker"\nversion = ")[^"]+("\n)',
                rf"\g<1>{args.version}\2",
                text,
            )
        if count != 1:
            raise ValueError("ISOLATED_VERSION_METADATA_MISMATCH")
        path.write_text(text, encoding="utf-8")
    metadata = source / f"water_sample_ranker-{args.version}.dist-info"
    metadata.mkdir()
    (metadata / "METADATA").write_text(
        f"Metadata-Version: 2.3\nName: water-sample-ranker\nVersion: {args.version}\n",
        encoding="utf-8",
    )
    manifest = {
        "schema": 1,
        "version": args.version,
        "source_version": SOURCE_VERSION,
        "source_commit": commit,
        "source_fingerprint": content_digest(inputs),
        "isolated_fingerprint": content_digest(build_inputs(source)),
        "python": sys.version.split()[0],
        "platform": sys.platform,
        "rating_methodology": "PENDING",
        "native_excel": "NOT_RUN",
        "human_windows_ux": "NOT_RUN",
    }
    manifest_path = source / "build-manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--clean",
        "--noconfirm",
        "--onedir",
        "--windowed",
        "--name",
        "Water_Sample_Ranker",
        "--paths",
        str(source / "src"),
        "--distpath",
        str(run / "dist"),
        "--workpath",
        str(run / "build"),
        "--specpath",
        str(run),
        "--add-data",
        f"{metadata}{os.pathsep}{metadata.name}",
        "--add-data",
        f"{manifest_path}{os.pathsep}.",
        str(source / "src/water_sample_ranker/__main__.py"),
    ]
    started = datetime.now(UTC).isoformat()
    log = run / "build.log"
    with log.open("xb") as stream:
        subprocess.run(command, cwd=source, stdout=stream, stderr=subprocess.STDOUT, check=True)
    bundle = run / "dist/Water_Sample_Ranker"
    (bundle / "templates").mkdir()
    for name in ("templates/Water_Sample_Ranking_Calculator.xlsx", "config.example.toml"):
        shutil.copyfile(source / name, bundle / name)
    shutil.copyfile(source / "docs/README_ПЕРВЫЙ_ЗАПУСК.txt", bundle / "README_ПЕРВЫЙ_ЗАПУСК.txt")
    shutil.copyfile(manifest_path, bundle / "build-manifest.json")
    binary = bundle / ("Water_Sample_Ranker.exe" if os.name == "nt" else "Water_Sample_Ranker")
    smoke_receipt = run / "frozen-smoke.json"
    with (run / "smoke.log").open("xb") as stream:
        subprocess.run(
            [
                str(binary),
                "--smoke-test",
                "--expected-version",
                args.version,
                "--expected-commit",
                commit,
                "--smoke-receipt",
                str(smoke_receipt),
            ],
            cwd=run,
            stdout=stream,
            stderr=subprocess.STDOUT,
            check=True,
            timeout=90,
            env=dict(os.environ, QT_QPA_PLATFORM="offscreen"),
        )
    if build_inputs(root) != inputs:
        raise ValueError("SOURCE_CHANGED_DURING_BUILD")
    receipt = {
        "status": "PASS",
        "scope": "FRESH_BUILD_FROZEN_GUI_CLI_TEMPLATE",
        "version": args.version,
        "source_commit": commit,
        "code_resource_fingerprint": content_digest(inputs),
        "inputs": inputs,
        "started_utc": started,
        "completed_utc": datetime.now(UTC).isoformat(),
        "binary": str(binary.relative_to(root)),
        "binary_fingerprint": fingerprint(binary),
        "build_log_sha256": fingerprint(log)["sha256"],
        "smoke_log_sha256": fingerprint(run / "smoke.log")["sha256"],
        "smoke": json.loads(smoke_receipt.read_text(encoding="utf-8")),
        "manifest": manifest,
        "platform": sys.platform,
        "python": sys.version.split()[0],
    }
    with (run / "build-receipt.json").open("x", encoding="utf-8") as stream:
        json.dump(receipt, stream, ensure_ascii=False, indent=2)
    pointer = root / "artifacts/private/frozen-current.json"
    pointer.write_text(
        json.dumps({"receipt": str((run / "build-receipt.json").relative_to(root))}),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "status": "PASS",
                "version": args.version,
                "receipt": str((run / "build-receipt.json").relative_to(root)),
            }
        )
    )


if __name__ == "__main__":
    main()
