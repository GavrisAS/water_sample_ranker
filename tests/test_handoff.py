"""Синтетические атаки на упаковку, без production fixtures."""

from __future__ import annotations

import io
import json
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

from scripts import verify_handoff as v
from water_sample_ranker.handoff import builder, evidence

PROJECT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "included,present,binary,status,reason",
    [
        (True, True, "TEMPLATE_BINARY_EXCLUDED", "PASS", "SCANNED_BLANK_TEMPLATE"),
        (True, False, "SCANNED_BLANK_TEMPLATE_INCLUDED", "PASS", "SCANNED_BLANK_TEMPLATE"),
        (False, True, "TEMPLATE_BINARY_EXCLUDED", "PARTIAL", "TEMPLATE_BINARY_EXCLUDED"),
        (True, True, "SCANNED_BLANK_TEMPLATE_INCLUDED", "PARTIAL", "SCANNED_BLANK_TEMPLATE"),
    ],
)
def test_template_binary_disagreement_is_rejected(included, present, binary, status, reason):
    with pytest.raises(v.HandoffError, match="TEMPLATE_BINARY_CONTEXT"):
        v.check_template_binding(
            included, present, {"binary": binary}, {"status": status, "reason": reason}
        )


def test_frozen_provenance_must_match_archived_source():
    inputs = {"src/water_sample_ranker/__init__.py": "d" * 64}
    gate = {
        "source_binding": "VERIFIED_CODE_RESOURCE_SNAPSHOT",
        "build_provenance": {
            "inputs": inputs,
            "source_commit": "a" * 40,
            "status": "PASS",
            "code_resource_fingerprint": v.sha(json.dumps(inputs, sort_keys=True).encode()),
        },
    }
    entries = {name: {"original_sha256": digest} for name, digest in inputs.items()}
    v.check_build_binding(gate, entries, "a" * 40)
    entries["src/water_sample_ranker/__init__.py"]["original_sha256"] = "e" * 64
    with pytest.raises(v.HandoffError, match="FROZEN_SOURCE_BINDING"):
        v.check_build_binding(gate, entries, "a" * 40)


def test_build_log_home_path_is_sanitized_before_privacy_scan(tmp_path):
    text = "Python environment: " + str(Path.home() / "synthetic-python/runtime")
    clean = evidence.clean_log(text, tmp_path)
    assert "<USER>" in clean and str(Path.home()) not in clean
    v.scan_text("synthetic-build.log", clean.encode())


def test_fresh_build_logs_obey_independent_archive_policy(tmp_path, monkeypatch):
    run = tmp_path / "synthetic-build"
    binary = run / "dist/Water_Sample_Ranker/Water_Sample_Ranker"
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"SYNTHETIC_BINARY_PLACEHOLDER")
    (run / "build.log").write_text("SYNTHETIC_BUILD_LOG")
    (run / "smoke.log").write_text("SYNTHETIC_SMOKE_LOG")
    monkeypatch.setattr(evidence, "frozen_binding", lambda root: (binary, {}))

    def fake_gate(command, root, timeout, label):
        data = b"SYNTHETIC_COMMAND_LOG"
        return {
            "status": "PASS",
            "log": f"review/logs/{label}.txt",
            "log_sha256": v.sha(data),
        }, data

    monkeypatch.setattr(evidence, "run_gate", fake_gate)
    _, logs, _ = evidence.checks(tmp_path, False, False, True, 10)
    review = {
        name: b"{}" if name.endswith(".json") else b"SYNTHETIC" for name in v.REQUIRED["review"]
    }
    review.update(logs)
    archive = tmp_path / "synthetic-review.tar.xz"
    builder.pack(archive, "review", "20260101T000000Z-abcdef012345", review)
    unpack = tmp_path / "extracted"
    unpack.mkdir()
    actual = v.read_archive(archive, "review", unpack)
    assert actual["review/logs/fresh_build.txt"] == b"SYNTHETIC_BUILD_LOG"
    assert actual["review/logs/fresh_smoke.txt"] == b"SYNTHETIC_SMOKE_LOG"


@pytest.fixture
def source(tmp_path: Path) -> Path:
    root = tmp_path / "проект с пробелами"
    root.mkdir()
    for name in builder.select_files(PROJECT, False):
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        from scripts.public_gate import PRIVATE_DOCUMENTS, public_document

        if name.startswith("docs/") and target.name in PRIVATE_DOCUMENTS:
            target.write_bytes(public_document(target.name))
        else:
            shutil.copyfile(PROJECT / name, target)
    subprocess.run(["git", "init", "-b", "main"], cwd=root, check=True, capture_output=True)
    return root


@pytest.fixture
def bundle(source: Path, tmp_path: Path) -> Path:
    return builder.build(source, source / "artifacts/handoff", builder.Options(run_checks=False))


def four(manifest: Path) -> list[Path]:
    m = v.parse_json(manifest.read_bytes(), seal=True)
    return [
        manifest,
        manifest.parent / m["verification"],
        *(manifest.parent / d["path"] for d in m["artifacts"].values()),
    ]


def test_four_files_without_data_or_git_and_independent_extracted_verifier(
    bundle: Path, tmp_path: Path
) -> None:
    assert len(four(bundle)) == 4
    m = v.parse_json(bundle.read_bytes(), seal=True)
    assert m["gates"]["production_validation"]["status"] == "NOT_RUN"
    assert m["source"]["git"]["head"] is None
    assert m["source"]["git"]["status"] == "PARTIAL"
    assert v.verify(bundle)["business_correctness"] == "UNKNOWN"
    clean = tmp_path / "только четыре файла"
    clean.mkdir()
    for file in four(bundle):
        shutil.copyfile(file, clean / file.name)
    with tarfile.open(bundle.parent / m["artifacts"]["review"]["path"]) as archive:
        stream = archive.extractfile("review/verify_handoff.py")
        assert stream is not None
        payload = stream.read()
    verifier = clean / "verify_handoff.py"
    verifier.write_bytes(payload)
    p = subprocess.run(
        [sys.executable, "-I", "-B", str(verifier), str(clean / bundle.name)],
        cwd=tmp_path,
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert p.returncode == 0, p.stdout.decode()
    assert b"PASS" in p.stdout
    with tarfile.open(bundle.parent / m["artifacts"]["audit"]["path"]) as archive:
        names = archive.getnames()
        assert not any(n.endswith((".xlsx", ".xlsm", ".csv")) for n in names)
        assert not any("/data/" in n or "/private/" in n for n in names)
        stream = archive.extractfile("audit/SOURCE_MANIFEST.json")
        assert stream is not None
        manifest = v.parse_json(stream.read())
        # Обезличенная копия уже может быть входом следующего review.
        assert all(
            bool(f["transformations"]) == (f["original_sha256"] != f["archived_sha256"])
            for f in manifest["files"].values()
        )


@pytest.mark.parametrize("which", [0, 1, 2, 3])
def test_any_output_byte_changed_fails(bundle: Path, which: int) -> None:
    file = four(bundle)[which]
    raw = bytearray(file.read_bytes())
    raw[len(raw) // 2] ^= 1
    file.write_bytes(raw)
    with pytest.raises((v.HandoffError, ValueError, OSError, KeyError)):
        v.verify(bundle)


@pytest.mark.parametrize(
    "name",
    [
        "../outside",
        "/absolute",
        "audit/../escape",
        "audit//empty",
        "audit/./a",
        "audit/AUX.txt",
        "audit/" + "C:" + "/escape",
        "audit/a" + chr(92) + "b",
        "audit/a\x00b",
        "audit/a.",
        "audit/" + "x" * 121,
    ],
)
def test_cross_platform_path_rejections(name: str) -> None:
    with pytest.raises(v.HandoffError):
        v.safe_path(name)


def rewrite_archive(manifest: Path, attack: str) -> None:
    m = v.parse_json(manifest.read_bytes(), seal=True)
    path = manifest.parent / m["artifacts"]["review"]["path"]
    with tarfile.open(path) as original:
        members = []
        for x in original:
            stream = original.extractfile(x)
            assert stream is not None
            members.append((x, stream.read()))
    with tarfile.open(path, "w:xz", format=tarfile.USTAR_FORMAT) as archive:
        for info, data in members:
            if attack == "missing" and info.name == "review/REPORT.md":
                continue
            archive.addfile(info, io.BytesIO(data))
        bad = tarfile.TarInfo("review/extra.txt")
        data = b"safe"
        if attack == "traversal":
            bad.name = "../escape"
        if attack == "forbidden":
            bad.name = "review/data/private.csv"
        if attack == "symlink":
            bad.type = tarfile.SYMTYPE
            bad.linkname = "outside"
        if attack == "hardlink":
            bad.type = tarfile.LNKTYPE
            bad.linkname = "review/REPORT.md"
        if attack == "fifo":
            bad.type = tarfile.FIFOTYPE
        if attack == "duplicate":
            bad.name = "review/REPORT.md"
        if attack == "collision":
            bad.name = "review/report.md"
        if attack == "huge":
            bad.size = v.MAX_FILE + 1
            data = b"x" * (v.MAX_FILE + 1)
        else:
            bad.size = len(data) if bad.isfile() else 0
        archive.addfile(bad, io.BytesIO(data) if bad.isfile() else None)
    m["artifacts"]["review"]["sha256"] = v.sha(path.read_bytes())
    m["artifacts"]["review"]["bytes"] = path.stat().st_size
    manifest.write_bytes(v.sealed(m))
    (manifest.parent / m["verification"]).unlink()


@pytest.mark.parametrize(
    "attack",
    [
        "traversal",
        "forbidden",
        "symlink",
        "hardlink",
        "fifo",
        "duplicate",
        "collision",
        "missing",
        "huge",
    ],
)
def test_forged_outer_hash_cannot_hide_hostile_archive(
    bundle: Path, attack: str, tmp_path: Path
) -> None:
    rewrite_archive(bundle, attack)
    with pytest.raises(v.HandoffError):
        v.verify(bundle, create_receipt=True)
    assert not (tmp_path / "escape").exists()


def test_stale_sidecar_context_and_partial_set(bundle: Path) -> None:
    original = bundle.read_bytes()
    m = v.parse_json(original, seal=True)
    m["source"]["fingerprint"] = "0" * 64
    bundle.write_bytes(v.sealed(m))
    with pytest.raises(v.HandoffError, match="STALE_SOURCE"):
        v.verify(bundle)
    bundle.write_bytes(original)
    (bundle.parent / m["artifacts"]["review"]["path"]).unlink()
    with pytest.raises(OSError):
        v.verify(bundle)


@pytest.mark.parametrize(
    "payload,category",
    [
        ("api_" + 'key = "' + "sk-" + "A" * 40 + '"', "TOKEN"),
        ("/" + "home" + "/private-user/notes", "OPERATOR_PATH"),
        (chr(92) * 2 + "server" + chr(92) + "private", "NETWORK_PATH"),
        ("a" + "@" + "private.example", "EMAIL"),
        (json.dumps({"well_id": "synthetic"}), "ROW_IDENTIFIERS"),
        (json.dumps({"rows": [{"sample": 1}]}), "RAW_ROWS"),
    ],
)
def test_content_scanner_fail_closed(payload: str, category: str) -> None:
    with pytest.raises(v.HandoffError, match=category):
        v.scan_text("report.json" if payload.startswith("{") else "report.md", payload.encode())


def test_secret_in_allowlisted_source_blocks_and_preserves_inputs(
    source: Path, tmp_path: Path
) -> None:
    data = source / "data"
    data.mkdir()
    private = data / "нельзя менять.xlsx"
    private.write_bytes(b"private-input")
    before = evidence.stamp(private)
    unsafe = source / "src/water_sample_ranker/unsafe.py"
    unsafe.write_text("access_" + 'token = "' + "ghp_" + "A" * 40 + '"')
    target = source / "artifacts/handoff"
    with pytest.raises(v.HandoffError, match="CONTENT_"):
        builder.build(source, target, builder.Options(run_checks=False))
    assert evidence.stamp(private) == before
    assert not list(target.glob("*.tar.*"))


def test_toctou_source_change_blocks_before_publication(
    source: Path, tmp_path: Path, monkeypatch
) -> None:
    original = evidence.checks

    def mutate(*args, **kwargs):
        result = original(*args, **kwargs)
        p = source / "README.md"
        p.write_text(p.read_text(encoding="utf-8") + "\nchanged\n", encoding="utf-8")
        return result

    monkeypatch.setattr(evidence, "checks", mutate)
    with pytest.raises(v.HandoffError, match="STALE_SOURCE"):
        builder.build(source, source / "artifacts/handoff", builder.Options(run_checks=False))
    assert not list((source / "artifacts/handoff").glob("*.tar.*"))


def test_git_dirty_untracked_and_no_head(source: Path) -> None:
    subprocess.run(["git", "init", "-b", "main"], cwd=source, check=True, capture_output=True)
    subprocess.run(["git", "add", "README.md"], cwd=source, check=True, capture_output=True)
    selected = builder.select_files(source, False)
    state, patches = builder.git_state(source, selected)
    assert state["head"] is None and state["status"] == "PARTIAL"
    assert state["staged"] == 1 and state["untracked"] > 0 and state["dirty"]
    assert b"README" in patches["evidence/STAGED.patch"]


def test_timeout_is_not_pass_and_log_is_sanitized(tmp_path: Path) -> None:
    gate, _ = evidence.run_gate(
        [sys.executable, "-c", "import time; time.sleep(5)"], tmp_path, 0.05, "timeout"
    )
    assert gate["status"] == "FAIL" and gate["returncode"] is None
    text = evidence.clean_log(str(tmp_path) + "/path", tmp_path)
    assert str(tmp_path) not in text
    win = "C:" + chr(92) + "Users" + chr(92) + "private-user" + chr(92) + "file"
    assert evidence.clean_log(win, tmp_path) == "<ABSOLUTE_PATH>"


def test_history_is_never_fresh_pass_and_invalid_production_is_safe(source: Path) -> None:
    p = source / "artifacts/private/delivery/qualification.json"
    p.parent.mkdir(parents=True)
    p.write_text(
        json.dumps({"calculation_date": "2020-01-01", "fields": {"береговое": {"rows": 3}}})
    )
    assert evidence.historical(source)["status"] == "UNKNOWN"
    assert evidence.historical(source)["fresh_execution"] is False
    assert evidence.production(source, False)["status"] == "NOT_RUN"
    assert evidence.production(source, True)["status"] == "FAIL"


def test_independent_failure_removes_only_own_published_files(
    source: Path, tmp_path: Path, monkeypatch
) -> None:
    target = source / "artifacts/handoff"
    target.mkdir(parents=True)
    old = target / "old-success.tar.gz"
    old.write_bytes(b"keep")
    original = subprocess.run

    def fail_second(command, *args, **kwargs):
        if "-I" in command and "--create-receipt" not in command:
            return subprocess.CompletedProcess(command, 2, b"", b"")
        return original(command, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", fail_second)
    with pytest.raises(v.HandoffError, match="INDEPENDENT"):
        builder.build(source, target, builder.Options(run_checks=False))
    assert list(target.iterdir()) == [old]


def test_template_binary_has_no_automatic_trust(source: Path) -> None:
    from openpyxl import load_workbook

    clean = (PROJECT / "templates/Water_Sample_Ranking_Calculator.xlsx").read_bytes()
    v.scan_xlsx(clean)
    path = source / "dirty.xlsx"
    path.write_bytes(clean)
    book = load_workbook(path)
    book["Данные"]["A7"] = "synthetic-sensitive"
    book.save(path)
    book.close()
    with pytest.raises(v.HandoffError, match="DATA_ROWS"):
        v.scan_xlsx(path.read_bytes())


def test_scanned_template_roundtrip_is_available_for_independent_review(source, tmp_path):
    name = "templates/Water_Sample_Ranking_Calculator.xlsx"
    target = source / name
    target.parent.mkdir(parents=True)
    target.write_bytes((PROJECT / name).read_bytes())
    manifest = builder.build(
        source,
        source / "artifacts/handoff",
        builder.Options(run_checks=False, include_template=True),
    )
    assert v.verify(manifest)["business_correctness"] == "UNKNOWN"
    m = v.parse_json(manifest.read_bytes(), seal=True)
    assert m["gates"]["template_binary"]["status"] == "PASS"
    with tarfile.open(manifest.parent / m["artifacts"]["audit"]["path"]) as a:
        stream = a.extractfile("audit/" + name)
        assert stream is not None and stream.read() == target.read_bytes()


def test_independent_verifier_works_with_legacy_windows_stdout(bundle):
    command = [
        sys.executable,
        "-I",
        "-B",
        "-c",
        "import sys, runpy; sys.stdout.reconfigure(encoding='cp1252'); "
        "script = sys.argv.pop(1); runpy.run_path(script, run_name='__main__')",
        str(PROJECT / "scripts/verify_handoff.py"),
        str(bundle),
    ]
    result = subprocess.run(command, capture_output=True, check=False)
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
    assert result.stdout.decode("ascii").startswith("PASS:")


@pytest.mark.parametrize("prefix", ["", "+", "-"])
def test_attribution_projection_keeps_credit_without_personal_name(prefix):
    person = "Александр" + " " + "Гаврись"
    raw = f"{prefix}Автор в исходном pyproject.toml — {person}.\n".encode()
    projected, changes = builder.projection(raw)
    v.scan_text("attribution.patch", projected)
    assert changes == ["HEADER_PERSONAL_ATTRIBUTION_REDACTED"]
    assert b"pyproject.toml" in projected and b"AUTHOR_REDACTED" in projected
    again, transformations = builder.projection(projected)
    assert again == projected and not transformations


def test_absolute_cli_no_cwd_dependency_and_readonly_project(source: Path, tmp_path: Path) -> None:
    before = {n: evidence.stamp(source / n) for n in builder.select_files(source, False)}
    result = subprocess.run(
        [
            sys.executable,
            str(source / "scripts/build_handoff.py"),
            "--skip-checks",
            "--output-dir",
            str(source / "artifacts/handoff"),
        ],
        cwd=tmp_path,
        capture_output=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout.decode()
    lines = result.stdout.decode().splitlines()
    assert len(lines) == 5
    assert all(
        line.startswith(str(source / "artifacts/handoff") + __import__("os").sep)
        for line in lines[:4]
    )
    assert len(list((source / "artifacts/handoff").iterdir())) == 4
    assert all(evidence.stamp(source / n) == fp for n, fp in before.items())


def test_unicode_normalization_and_reserved_confusables() -> None:
    assert v.safe_path("audit/caf\u00e9.py") == v.safe_path("audit/cafe\u0301.py")
    with pytest.raises(v.HandoffError):
        v.safe_path("audit/\uff0e\uff0e/escape")
    with pytest.raises(v.HandoffError):
        v.safe_path("audit/\uff23\uff2f\uff2e.txt")


def test_data_is_not_parsed_without_flag(source: Path, monkeypatch) -> None:
    from water_sample_ranker import config

    def forbidden(*args, **kwargs):
        raise AssertionError("must not discover")

    monkeypatch.setattr(config, "load_config", forbidden)
    assert evidence.production(source, False)["status"] == "NOT_RUN"
    paths, safe = evidence.input_inventory(source)
    assert safe["row_data_parsed"] is False


def test_input_mutation_is_blocked(source: Path, tmp_path: Path, monkeypatch) -> None:
    data = source / "data"
    data.mkdir()
    p = data / "input.bin"
    p.write_bytes(b"before")
    original = evidence.checks

    def mutate(*args, **kwargs):
        result = original(*args, **kwargs)
        p.write_bytes(b"after")
        return result

    monkeypatch.setattr(evidence, "checks", mutate)
    with pytest.raises(v.HandoffError, match="INPUTS_CHANGED"):
        builder.build(source, source / "artifacts/handoff", builder.Options(run_checks=False))
    assert not list((source / "artifacts/handoff").glob("*.tar.*"))


def test_tar_hidden_trailer_rejected_even_with_new_outer_hash(bundle: Path, tmp_path: Path) -> None:
    import lzma

    m = v.parse_json(bundle.read_bytes(), seal=True)
    path = bundle.parent / m["artifacts"]["review"]["path"]
    raw = lzma.decompress(path.read_bytes()) + b"hidden-content"
    path.write_bytes(lzma.compress(raw))
    with pytest.raises(v.HandoffError, match="TRAILER"):
        v.read_archive(path, "review", tmp_path / "fresh")


def test_git_executable_unavailable_is_partial(source: Path, monkeypatch) -> None:
    original = subprocess.run

    def unavailable(command, *args, **kwargs):
        if command[0] == "git":
            raise FileNotFoundError()
        return original(command, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", unavailable)
    state, patches = builder.git_state(source, builder.select_files(source, False))
    assert state["status"] == "PARTIAL" and state["head"] is None and not patches


def test_git_totals_include_excluded_paths_without_disclosing_names(source: Path) -> None:
    subprocess.run(["git", "init", "-b", "main"], cwd=source, check=True, capture_output=True)
    (source / "private-note.txt").write_text("not for handoff")
    subprocess.run(
        ["git", "add", "README.md", "private-note.txt"], cwd=source, check=True, capture_output=True
    )
    state, _ = builder.git_state(source, builder.select_files(source, False))
    assert state["staged"] == 1
    assert state["total_state_counts"]["staged"] == 2
    assert state["porcelain_scope"] == "ALLOWLIST_FILTERED"
    assert "private-note" not in v.canonical(state).decode()


def test_oversized_sidecar_rejected_before_read(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "oversized.json"
    with path.open("wb") as stream:
        stream.truncate(v.MAX_FILE + 1)

    def forbidden(*args, **kwargs):
        raise AssertionError("unbounded read forbidden")

    monkeypatch.setattr(Path, "read_bytes", forbidden)
    with pytest.raises(v.HandoffError, match="SIZE"):
        v.verify(path)


@pytest.mark.parametrize(
    "requested",
    [
        "elsewhere",
        "artifacts/handoff/nested",
        "artifacts/../artifacts/handoff",
        "C:" + chr(92) + "handoff",
    ],
)
def test_alternative_output_is_blocked_before_writes(source, requested):
    with pytest.raises(v.HandoffError, match="NONCANONICAL"):
        builder.build(source, Path(requested), builder.Options(run_checks=False))
    assert not (source / "artifacts").exists()


@pytest.mark.parametrize("kind", ["output", "parent", "alias"])
def test_output_symlinks_are_rejected_before_writes(source, tmp_path, kind):
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    output = source / "artifacts/handoff"
    try:
        if kind == "parent":
            (source / "artifacts").symlink_to(outside, target_is_directory=True)
        elif kind == "output":
            output.parent.mkdir()
            output.symlink_to(outside, target_is_directory=True)
        else:
            output.mkdir(parents=True)
            alias = source / "alias"
            alias.symlink_to(output, target_is_directory=True)
            output = alias
    except OSError:
        pytest.skip("OS does not grant symlink creation")
    with pytest.raises(v.HandoffError, match="SYMLINK"):
        builder.build(source, output, builder.Options(run_checks=False))
    assert not list(outside.iterdir())


def test_nested_or_forged_git_root_cannot_supply_final_directory(source):
    nested = source / "nested"
    nested.mkdir()
    shutil.copyfile(source / "pyproject.toml", nested / "pyproject.toml")
    (nested / "scripts").mkdir()
    shutil.copyfile(source / "scripts/build_handoff.py", nested / "scripts/build_handoff.py")
    with pytest.raises(v.HandoffError, match="ROOT_UNCONFIRMED"):
        builder.build(nested, nested / "artifacts/handoff", builder.Options(run_checks=False))
    subprocess.run(["git", "init"], cwd=nested, capture_output=True, check=True)
    with pytest.raises(v.HandoffError, match="ROOT_UNCONFIRMED"):
        builder.build(nested, nested / "artifacts/handoff", builder.Options(run_checks=False))
    assert not (nested / "artifacts").exists()


@pytest.mark.parametrize("fail_at", [1, 2, 3, 4])
def test_each_publication_failure_removes_incomplete_set_only(source, monkeypatch, fail_at):
    import os

    output = source / "artifacts/handoff"
    output.mkdir(parents=True)
    old = output / "old.tar.gz"
    old.write_bytes(b"previous set preserved")
    original = os.link
    calls = 0

    def fail(source_file, target):
        nonlocal calls
        calls += 1
        if calls == fail_at:
            raise OSError("injected write failure")
        return original(source_file, target)

    monkeypatch.setattr(os, "link", fail)
    with pytest.raises(OSError, match="injected"):
        builder.build(source, output, builder.Options(run_checks=False))
    assert list(output.iterdir()) == [old]
    assert old.read_bytes() == b"previous set preserved"
    assert not list(output.parent.glob(".handoff-stage-*"))


def test_repeated_id_never_overwrites_old_set(source, monkeypatch):
    class FixedTime:
        @staticmethod
        def now(tz):
            from datetime import datetime

            return datetime(2026, 1, 1, tzinfo=tz)

    class FixedID:
        hex = "abcdef01234500000000000000000000"

    monkeypatch.setattr(builder, "datetime", FixedTime)
    monkeypatch.setattr(builder, "uuid4", FixedID)
    output = source / "artifacts/handoff"
    first = builder.build(source, output, builder.Options(run_checks=False))
    before = {p.name: p.read_bytes() for p in four(first)}
    with pytest.raises(v.HandoffError, match="OUTPUT_COLLISION"):
        builder.build(source, output, builder.Options(run_checks=False))
    assert {p.name: p.read_bytes() for p in output.iterdir()} == before


def test_directory_substitution_is_blocked(source, monkeypatch):
    original = evidence.checks
    output = source / "artifacts/handoff"

    def substitute(*args, **kwargs):
        result = original(*args, **kwargs)
        output.rename(output.with_name("preserved-original"))
        output.mkdir()
        return result

    monkeypatch.setattr(evidence, "checks", substitute)
    with pytest.raises(v.HandoffError, match="DIRECTORY_CHANGED"):
        builder.build(source, output, builder.Options(run_checks=False))
    assert not list(output.iterdir())


def test_rollback_preserves_replacement_owned_by_someone_else(source, monkeypatch):
    import os

    original = os.link
    output = source / "artifacts/handoff"
    replaced = None

    def fail(source_file, target):
        nonlocal replaced
        if replaced is None:
            original(source_file, target)
            replaced = target
            return
        replaced.unlink()
        replaced.write_bytes(b"foreign crash evidence")
        raise OSError("injected replacement")

    monkeypatch.setattr(os, "link", fail)
    with pytest.raises(OSError, match="injected"):
        builder.build(source, output, builder.Options(run_checks=False))
    assert list(output.iterdir()) == [replaced]
    assert replaced is not None
    assert replaced.read_bytes() == b"foreign crash evidence"


def test_private_documents_and_their_diffs_are_projected_without_original_text(source):
    from scripts.public_gate import public_document

    path = source / "docs/OPEN_QUESTIONS.md"
    marker = "SYNTHETIC_" + "PRIVATE_DOCUMENT_BYTES"
    path.write_text(marker)
    subprocess.run(["git", "add", "docs/OPEN_QUESTIONS.md"], cwd=source, check=True)
    payload, entries, _ = builder.snapshot(source, builder.Options(run_checks=False))
    assert payload["audit/docs/OPEN_QUESTIONS.md"] == public_document(path.name)
    assert entries["docs/OPEN_QUESTIONS.md"]["transformations"] == ["PRIVATE_DOCUMENT_REPLACED"]
    assert not any(marker.encode() in data for data in payload.values())
    assert path.read_text() == marker


def test_public_projection_binds_final_commit_but_never_moves_final_output(tmp_path):
    from scripts.prepare_public import prepare
    from scripts.public_gate import allowed_names

    real = tmp_path / "реальный проект"
    prepare(PROJECT, real)
    subprocess.run(["git", "init", "-b", "main"], cwd=real, check=True, capture_output=True)
    public = real / "artifacts/private/public"
    prepare(real, public)
    for args in (
        ["init", "-b", "main"],
        ["config", "user.name", "DEMO"],
        ["config", "user.email", "demo@" + "users.noreply.github.com"],
        ["add", "--", *sorted(allowed_names(public))],
        ["commit", "-m", "SYNTHETIC PROJECTION"],
    ):
        subprocess.run(["git", *args], cwd=public, check=True, capture_output=True)
    output = real / "artifacts/handoff"
    result = builder.build(real, output, builder.Options(run_checks=False), source_root=public)
    manifest = v.parse_json(result.read_bytes(), seal=True)
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=public, text=True).strip()
    assert manifest["source"]["git"]["head"] == head
    assert result.parent == output and len(list(output.iterdir())) == 4
    assert not (public / "artifacts/handoff").exists()
    assert v.verify(result)["business_correctness"] == "UNKNOWN"
    with pytest.raises(v.HandoffError, match="ROOT_UNCONFIRMED"):
        builder.build(public, public / "artifacts/handoff", builder.Options(run_checks=False))
    (real / "README.md").write_text("SYNTHETIC CHANGE AFTER PUBLIC COMMIT")
    before = {p.name: p.read_bytes() for p in output.iterdir()}
    with pytest.raises(v.HandoffError, match="PROJECTION_MISMATCH"):
        builder.build(real, output, builder.Options(run_checks=False), source_root=public)
    assert before == {p.name: p.read_bytes() for p in output.iterdir()}
