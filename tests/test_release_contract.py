"""Reservations and source/version/asset binding, using synthetic Windows containers."""

import shutil
import struct
import tomllib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from scripts import publish_release as publisher
from scripts import release_assets as assets
from scripts import verify_handoff as policy
from water_sample_ranker import SOURCE_VERSION, __version__
from water_sample_ranker.build_provenance import content_digest
from water_sample_ranker.versioning import parse_release_tag, parse_version, reserved_tag

ROOT = Path(__file__).resolve().parents[1]
COMMIT = "a" * 40
INPUTS = {"src/water_sample_ranker/__init__.py": "b" * 64}


def tag(number=1, **overrides):
    return reserved_tag(
        event=overrides.get("event", "push"),
        ref=overrides.get("ref", "refs/heads/main"),
        workflow=overrides.get("workflow", ".github/workflows/release-main.yml"),
        run_number=overrides.get("run_number", number),
    )


def test_three_component_source_runtime_and_numeric_patch():
    assert parse_release_tag("v0.1.1") == (0, 1, 1)
    assert parse_release_tag("v0.1.10") == (0, 1, 10)
    assert parse_release_tag(tag(10)) > parse_release_tag(tag(9))
    assert (
        tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
        == SOURCE_VERSION
        == __version__
        == "0.1.1"
    )


@pytest.mark.parametrize(
    "version", ["0.1", "v0.1.1", "0.01.1", "0.1.01", "0.1.1rc1", " 0.1.1", "0.1.1\n"]
)
def test_versions_are_strict(version):
    with pytest.raises(ValueError):
        parse_version(version)


def test_first_push_rerun_failed_reservation_and_concurrency():
    assert tag() == tag() == "v0.1.1"  # rerun has the same run_number
    assert tag(2) == "v0.1.2"  # first failed run never returns its number
    with ThreadPoolExecutor(max_workers=8) as pool:
        versions = list(pool.map(tag, range(1, 50)))
    assert len(versions) == len(set(versions)) and versions[-1] == "v0.1.49"


@pytest.mark.parametrize(
    "override",
    [
        {"event": "workflow_dispatch"},
        {"event": "pull_request"},
        {"ref": "refs/tags/v0.1.1"},
        {"workflow": ".github/workflows/build-windows.yml"},
        {"run_number": 0},
    ],
)
def test_manual_pr_tag_and_unrelated_workflows_are_ineligible(override):
    with pytest.raises(ValueError):
        tag(**override)


def pe():
    data = bytearray(128)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 60, 80)
    data[80:84] = b"PE\0\0"
    struct.pack_into("<H", data, 84, 0x8664)
    return bytes(data)


def synthetic_assets(directory):
    directory.mkdir()
    version = "0.1.1"
    manifest = {"version": version, "source_commit": COMMIT, "platform": "win32"}
    files = {
        "Water_Sample_Ranker.exe": pe(),
        "_internal/python314.dll": b"SYNTHETIC DLL",
        "templates/Water_Sample_Ranking_Calculator.xlsx": (
            ROOT / "templates/Water_Sample_Ranking_Calculator.xlsx"
        ).read_bytes(),
        "config.example.toml": b"# SYNTHETIC CONFIG\n",
        assets.README: b"SYNTHETIC PREVIEW\n",
        "build-manifest.json": policy.canonical(manifest),
        "_internal/build-manifest.json": policy.canonical(manifest),
        "_internal/water_sample_ranker-0.1.1.dist-info/METADATA": (
            b"Name: water-sample-ranker\nVersion: 0.1.1\n"
        ),
    }
    inventory = {n: {"sha256": policy.sha(d), "bytes": len(d)} for n, d in files.items()}
    files["bundle-inventory.json"] = policy.canonical(inventory)
    names = assets.asset_names(version)
    assets.write_zip(
        directory / names[0], {"Water_Sample_Ranker/" + n: d for n, d in files.items()}
    )
    evidence = {
        "schema": 1,
        "version": version,
        "source_commit": COMMIT,
        "run_id": "100",
        "run_number": 1,
        "inputs": INPUTS,
        "source_fingerprint": content_digest(INPUTS),
        "build_manifest": manifest,
        "package_sha256": policy.sha((directory / names[0]).read_bytes()),
        "binary_sha256": policy.sha(pe()),
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
        "smoke": {
            "status": "PASS",
            "version": version,
            "package_version": version,
            "gui_version": version,
            "source_commit": COMMIT,
        },
    }
    assets.write_zip(
        directory / names[2],
        {"BUILD_EVIDENCE.json": policy.canonical(evidence), "README.txt": b"SYNTHETIC ONLY\n"},
    )
    for n in (names[0], names[2]):
        (directory / (n + ".sha256")).write_text(
            f"{policy.sha((directory / n).read_bytes())}  {n}\n"
        )
    return directory


def verify(directory, **changes):
    return assets.verify_release_assets(
        directory,
        version=changes.get("version", "0.1.1"),
        commit=changes.get("commit", COMMIT),
        run_id=changes.get("run_id", "100"),
        run_number=changes.get("run_number", 1),
        inputs=changes.get("inputs", INPUTS),
    )


@pytest.mark.parametrize(
    "mismatch", ["commit", "version", "run_id", "run_number", "inputs", "sha", "extra_asset"]
)
def test_source_tag_binary_assets_cannot_disagree(tmp_path, mismatch):
    directory = synthetic_assets(tmp_path / "assets")
    assert verify(directory)["version"] == "0.1.1"
    changes = {}
    if mismatch == "sha":
        (directory / assets.asset_names("0.1.1")[0]).write_bytes(b"TRUNCATED")
    elif mismatch == "extra_asset":
        (directory / "unreviewed.txt").write_bytes(b"SYNTHETIC UNREVIEWED")
    else:
        changes[mismatch] = {
            "commit": "c" * 40,
            "version": "0.1.2",
            "run_id": "101",
            "run_number": 2,
            "inputs": {},
        }[mismatch]
    with pytest.raises(ValueError):
        verify(directory, **changes)


def test_rerun_candidate_is_same_run_and_latest_available_attempt():
    items = [
        {"name": "candidate-v0.1.1-100-1"},
        {"name": "candidate-v0.1.1-100-2"},
        {"name": "candidate-v0.1.1-101-3"},
    ]
    assert publisher.candidate_name(items, "0.1.1", "100", 1).endswith("-1")
    assert publisher.candidate_name(items, "0.1.1", "100", 3).endswith("-2")


@pytest.mark.parametrize("conflict", [False, True])
def test_existing_release_verifies_or_fails_without_mutation(tmp_path, monkeypatch, conflict):
    directory = synthetic_assets(tmp_path / "assets")
    calls = []
    release = {
        "tag_name": "v0.1.1",
        "prerelease": True,
        "draft": False,
        "assets": [{"name": n} for n in assets.asset_names("0.1.1")],
    }

    def fake_gh(*args, **kwargs):
        calls.append(args)
        if args[:2] == ("release", "download"):
            dest = Path(args[args.index("--dir") + 1])
            for file in directory.iterdir():
                shutil.copyfile(file, dest / file.name)
            return None
        if "/releases/tags/" in args[1]:
            return release
        return {
            "object": {
                "sha": "d" * 40 if conflict else COMMIT,
                "type": "commit",
                "url": "synthetic",
            }
        }

    monkeypatch.setattr(publisher, "gh", fake_gh)
    monkeypatch.setattr(publisher, "build_inputs", lambda root: INPUTS)
    if conflict:
        with pytest.raises(ValueError, match="CONFLICT_FAIL"):
            publisher.verify_existing(ROOT, "DEMO/repo", "v0.1.1", COMMIT, "100", 1)
    else:
        assert publisher.verify_existing(ROOT, "DEMO/repo", "v0.1.1", COMMIT, "100", 1) == release
    assert all(args[0] == "api" or args[:2] == ("release", "download") for args in calls)


def test_remote_job_failure_blocks_publication():
    jobs = [
        {"name": n, "status": "completed", "conclusion": "success"}
        for n in ("linux-checks / checks (ubuntu-latest)", "windows-build / build")
    ]
    publisher.verify_jobs(jobs)
    jobs[1]["conclusion"] = "failure"
    with pytest.raises(ValueError):
        publisher.verify_jobs(jobs)
