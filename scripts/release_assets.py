"""Build and independently re-read the four strictly public release assets."""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import struct
import subprocess
import sys
from pathlib import Path
from typing import Any
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from scripts import public_gate as privacy
from scripts import verify_handoff as policy
from water_sample_ranker.build_provenance import build_inputs, content_digest
from water_sample_ranker.versioning import parse_version

APP = "Water_Sample_Ranker"
README = "README_ПЕРВЫЙ_ЗАПУСК.txt"


def asset_names(version: str) -> tuple[str, str, str, str]:
    parse_version(version)
    a = f"{APP}-v{version}-windows-x64.zip"
    b = f"{APP}-v{version}-release-evidence.zip"
    return a, a + ".sha256", b, b + ".sha256"


def write_zip(path: Path, files: dict[str, bytes]) -> None:
    with ZipFile(path, "x", compression=ZIP_DEFLATED) as archive:
        for name, data in sorted(files.items()):
            info = ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, data)


def binary_privacy(data: bytes) -> None:
    for marker in (b"SENSITIVE_" + b"PRODUCTION_SENTINEL", b"-----BEGIN " + b"PRIVATE KEY-----"):
        if marker in data or marker.decode().encode("utf-16le") in data:
            raise policy.HandoffError("PUBLIC_BINARY_CONTENT")
    if re.search(rb"(?:gh[pous]_|github_pat_)[A-Za-z0-9_]{25,}", data):
        raise policy.HandoffError("PUBLIC_BINARY_TOKEN")


def check_pe_x64(data: bytes) -> None:
    if len(data) < 64 or data[:2] != b"MZ":
        raise ValueError("RELEASE_REQUIRES_WINDOWS_EXE")
    offset = struct.unpack_from("<I", data, 60)[0]
    if (
        data[offset : offset + 4] != b"PE\0\0"
        or struct.unpack_from("<H", data, offset + 4)[0] != 0x8664
    ):
        raise ValueError("RELEASE_REQUIRES_WINDOWS_X64")


def check_onedir(files: dict[str, bytes], version: str, commit: str) -> dict[str, Any]:
    prefix = APP + "/"
    if not files or any(not name.startswith(prefix) for name in files):
        raise ValueError("ONEDIR_ROOT")
    relative = {name[len(prefix) :]: data for name, data in files.items()}
    top = {
        APP + ".exe",
        "templates/Water_Sample_Ranking_Calculator.xlsx",
        "config.example.toml",
        README,
        "build-manifest.json",
        "bundle-inventory.json",
    }
    if not top <= relative.keys():
        raise ValueError("ONEDIR_REQUIRED_RESOURCES")
    for name, data in relative.items():
        privacy.check_name(name)
        if name in top:
            if name.endswith(".xlsx"):
                privacy.check_template(data)
            elif name.endswith(".exe"):
                check_pe_x64(data)
                binary_privacy(data)
            else:
                policy.scan_text(name, data)
            continue
        if not name.startswith("_internal/"):
            raise ValueError("ONEDIR_UNEXPECTED_FILE")
        tail = name.removeprefix("_internal/")
        if tail == "base_library.zip":
            with ZipFile(io.BytesIO(data)) as nested:
                seen = set()
                for info in nested.infolist():
                    key = policy.safe_path(info.filename)
                    if (
                        key in seen
                        or not info.filename.endswith(".pyc")
                        or info.file_size > 8 * 1024**2
                    ):
                        raise ValueError("ONEDIR_LIBRARY_MEMBER")
                    seen.add(key)
                    binary_privacy(nested.read(info))
            continue
        if (
            tail == "build-manifest.json"
            or tail == f"water_sample_ranker-{version}.dist-info/METADATA"
        ):
            policy.scan_text(name, data)
            continue
        if Path(tail).suffix.casefold() not in {".dll", ".pyd", ".qm"}:
            raise ValueError("ONEDIR_UNREVIEWED_RESOURCE")
        binary_privacy(data)
    if not any(n.startswith("_internal/") and n.endswith(".dll") for n in relative):
        raise ValueError("ONEDIR_INTERNAL_MISSING")
    manifest = json.loads(relative["build-manifest.json"])
    if (
        manifest.get("version") != version
        or manifest.get("source_commit") != commit
        or manifest.get("platform") != "win32"
    ):
        raise ValueError("ONEDIR_VERSION_COMMIT")
    if relative.get("_internal/build-manifest.json") != relative["build-manifest.json"]:
        raise ValueError("ONEDIR_EMBEDDED_MANIFEST")
    metadata = relative.get(f"_internal/water_sample_ranker-{version}.dist-info/METADATA", b"")
    if f"\nVersion: {version}\n".encode() not in metadata:
        raise ValueError("ONEDIR_PACKAGE_METADATA")
    inventory = json.loads(relative["bundle-inventory.json"])
    actual = {
        n: {"sha256": policy.sha(d), "bytes": len(d)}
        for n, d in relative.items()
        if n != "bundle-inventory.json"
    }
    if inventory != actual:
        raise ValueError("ONEDIR_INVENTORY_MISMATCH")
    return manifest


def verify_release_assets(
    directory: Path,
    *,
    version: str,
    commit: str,
    run_id: str,
    run_number: int,
    inputs: dict[str, str],
) -> dict[str, Any]:
    names = asset_names(version)
    if {p.name for p in directory.iterdir()} != set(names):
        raise ValueError("RELEASE_EXACT_ASSET_SET_REQUIRED")
    for name in (names[0], names[2]):
        data = policy.limited_read(directory / name, 512 * 1024**2)
        expected = f"{policy.sha(data)}  {name}\n".encode()
        if (directory / (name + ".sha256")).read_bytes() != expected:
            raise ValueError("RELEASE_CHECKSUM")
    files = privacy.checked_zip_members(directory / names[0])
    manifest = check_onedir(files, version, commit)
    review = privacy.checked_zip_members(directory / names[2])
    if set(review) != {"BUILD_EVIDENCE.json", "README.txt"}:
        raise ValueError("RELEASE_EVIDENCE_ALLOWLIST")
    for name, data in review.items():
        policy.scan_text(name, data)
    ev = json.loads(review["BUILD_EVIDENCE.json"])
    if (
        ev.get("schema") != 1
        or ev.get("version") != version
        or ev.get("source_commit") != commit
        or ev.get("run_id") != run_id
        or ev.get("run_number") != run_number
        or ev.get("inputs") != inputs
        or ev.get("source_fingerprint") != content_digest(inputs)
        or ev.get("build_manifest") != manifest
        or ev.get("package_sha256") != policy.sha((directory / names[0]).read_bytes())
        or ev.get("binary_sha256") != policy.sha(files[f"{APP}/{APP}.exe"])
        or any(
            ev.get("gates", {}).get(k) != "PASS"
            for k in (
                "linux_checks",
                "windows_checks",
                "windows_build",
                "frozen_gui_cli",
                "synthetic_frozen_update_all",
                "privacy_source_history",
                "privacy_archives",
                "empty_score_rank_top",
            )
        )
        or any(
            ev.get("human_gates", {}).get(k) != "NOT_RUN" for k in ("native_excel", "windows_ux")
        )
        or ev.get("production_qualification") != "NOT_RUN"
    ):
        raise ValueError("RELEASE_EVIDENCE_BINDING")
    smoke = ev.get("smoke", {})
    if (
        smoke.get("status") != "PASS"
        or any(smoke.get(k) != version for k in ("version", "package_version", "gui_version"))
        or smoke.get("source_commit") != commit
    ):
        raise ValueError("RELEASE_BINARY_VERSION_BINDING")
    return ev


def package(root: Path, version: str, commit: str, run_id: str, run_number: int) -> Path:
    privacy.scan_tree(root)
    if os.environ.get("LINUX_CHECKS_RESULT") != "success":
        raise ValueError("RELEASE_LINUX_CHECKS_REQUIRED")
    if (
        sys.platform != "win32"
        or subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
        != commit
    ):
        raise ValueError("RELEASE_WINDOWS_SOURCE_REQUIRED")
    inputs = build_inputs(root)
    pointer = json.loads(
        (root / "artifacts/private/frozen-current.json").read_text(encoding="utf-8")
    )
    receipt = json.loads((root / pointer["receipt"]).read_text(encoding="utf-8"))
    binary = root / receipt["binary"]
    if (
        receipt.get("status") != "PASS"
        or receipt.get("version") != version
        or receipt.get("source_commit") != commit
        or receipt.get("inputs") != inputs
        or receipt.get("binary_fingerprint", {}).get("sha256") != policy.sha(binary.read_bytes())
    ):
        raise ValueError("RELEASE_FRESH_BUILD_REQUIRED")
    frozen_cli = json.loads(
        (binary.parent.parent.parent / "frozen-cli.json").read_text(encoding="utf-8")
    )
    if frozen_cli != {
        "status": "PASS",
        "version": version,
        "source_commit": commit,
        "empty_score_rank_top": True,
    }:
        raise ValueError("RELEASE_FROZEN_CLI_REQUIRED")
    checks = json.loads((root / "artifacts/private/checks.json").read_text(encoding="utf-8"))
    if checks.get("status") != "PASS" or checks.get("source_commit") != commit:
        raise ValueError("RELEASE_CHECKS_REQUIRED")
    payloads = {}
    for path in binary.parent.rglob("*"):
        if path.is_symlink():
            raise ValueError("RELEASE_SYMLINK")
        if path.is_file():
            payloads[path.relative_to(binary.parent).as_posix()] = path.read_bytes()
    payloads.pop("bundle-inventory.json", None)
    inventory = {n: {"sha256": policy.sha(d), "bytes": len(d)} for n, d in payloads.items()}
    payloads["bundle-inventory.json"] = policy.canonical(inventory)
    files = {f"{APP}/{n}": d for n, d in payloads.items()}
    check_onedir(files, version, commit)
    destination = root / "artifacts/private/release-assets"
    destination.mkdir(parents=True, exist_ok=False)
    names = asset_names(version)
    write_zip(destination / names[0], files)
    evidence = {
        "schema": 1,
        "version": version,
        "source_commit": commit,
        "run_id": run_id,
        "run_number": run_number,
        "source_fingerprint": content_digest(inputs),
        "inputs": inputs,
        "build_manifest": receipt["manifest"],
        "binary_sha256": policy.sha(binary.read_bytes()),
        "package_sha256": policy.sha((destination / names[0]).read_bytes()),
        "smoke": receipt["smoke"],
        "python": receipt["python"],
        "uv": subprocess.check_output(["uv", "--version"], text=True).strip(),
        "synthetic_tests": checks["pytest"],
        "gates": {
            k: "PASS"
            for k in (
                "linux_checks",
                "windows_checks",
                "windows_build",
                "frozen_gui_cli",
                "synthetic_frozen_update_all",
                "privacy_source_history",
                "privacy_archives",
                "empty_score_rank_top",
            )
        },
        "human_gates": {"native_excel": "NOT_RUN", "windows_ux": "NOT_RUN"},
        "production_qualification": "NOT_RUN",
        "rating_methodology": "PENDING",
    }
    review = {
        "BUILD_EVIDENCE.json": policy.canonical(evidence),
        "README.txt": (
            "TECHNICAL PREVIEW — НЕ УТВЕРЖДЁННЫЙ РЕЙТИНГ\n"
            "Только автоматические синтетические проверки и Windows frozen smoke.\n"
            "Производственные источники не включены и здесь не квалифицированы.\n"
            "Native Excel и ручная Windows UX: NOT_RUN. Методика: PENDING.\n"
            "Баллы/ранги/TOP пусты; №7–9 — наблюдения/кандидаты, UNKNOWN.\n"
            "Это отдельный публичный evidence; приватный handoff не прикладывается.\n"
        ).encode(),
    }
    for name, data in review.items():
        policy.scan_text(name, data)
    write_zip(destination / names[2], review)
    for name in (names[0], names[2]):
        (destination / (name + ".sha256")).write_text(
            f"{policy.sha((destination / name).read_bytes())}  {name}\n", encoding="ascii"
        )
    verify_release_assets(
        destination,
        version=version,
        commit=commit,
        run_id=run_id,
        run_number=run_number,
        inputs=inputs,
    )
    return destination


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--version", required=True)
    p.add_argument("--source-sha", required=True)
    p.add_argument("--run-id", required=True)
    p.add_argument("--run-number", required=True, type=int)
    args = p.parse_args()
    package(Path.cwd(), args.version, args.source_sha, args.run_id, args.run_number)
    print("PUBLIC_RELEASE_ASSETS_VERIFIED")


if __name__ == "__main__":
    main()
