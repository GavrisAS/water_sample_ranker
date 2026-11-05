"""Local temporary leak fixtures never enter this repository or public archives."""

import io
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path
from zipfile import ZipFile

import pytest

from scripts import public_gate as gate
from scripts import verify_handoff as policy

ROOT = Path(__file__).resolve().parents[1]


def command(root, *args):
    return subprocess.check_output(["git", *args], cwd=root, stderr=subprocess.DEVNULL)


@pytest.fixture
def repository(tmp_path):
    root = tmp_path / "public"
    root.mkdir()
    command(root, "init", "-b", "main")
    command(root, "config", "user.name", "DEMO")
    command(root, "config", "user.email", "demo@" + "users.noreply.github.com")
    (root / "scripts").mkdir()
    (root / "README.md").write_text("SYNTHETIC PUBLIC TREE\n")
    (root / "scripts/public_files.txt").write_text("README.md\nscripts/public_files.txt\n")
    command(root, "add", ".")
    command(root, "commit", "-m", "SYNTHETIC ROOT")
    return root


def test_clean_history_and_git_archive(repository):
    result = gate.scan_tree(repository)
    assert result["status"] == "PASS" and result["commits"] == 1
    archive = repository / "source.tar"
    archive.write_bytes(command(repository, "archive", "HEAD"))
    assert gate.scan_source_archive(archive, repository)["status"] == "PASS"


def test_deleted_leak_is_still_blocked_in_full_history(repository):
    leak = repository / "private.txt"
    leak.write_text("SENSITIVE_" + "PRODUCTION_SENTINEL")
    command(repository, "add", "private.txt")
    command(repository, "commit", "-m", "SYNTHETIC LEAK FOR NEGATIVE TEST")
    command(repository, "rm", "private.txt")
    command(repository, "commit", "-m", "SYNTHETIC DELETE IS NOT ERASURE")
    with pytest.raises(policy.HandoffError):
        gate.scan_tree(repository)


def test_allowlisted_python_content_leak_is_blocked():
    with pytest.raises(policy.HandoffError, match="PUBLIC_LEAK_CANARY"):
        gate.check_source(
            "scripts/demo.py", ("SENSITIVE_" + "PRODUCTION_SENTINEL").encode(), {"scripts/demo.py"}
        )


@pytest.mark.parametrize(
    "name",
    [
        "data/source.bin",
        "artifacts/private/file.py",
        "gdis_loader/reference.py",
        ".env.local",
        "Вопросы_заказчику.xlsx",
    ],
)
def test_denylist_is_not_extension_based(name):
    with pytest.raises(policy.HandoffError):
        gate.check_source(name, b"SYNTHETIC", {name})


def test_original_customer_document_is_never_public_even_if_allowlisted():
    name = "docs/CUSTOMER_DECISIONS_STAGE_B.md"
    gate.check_source(name, gate.public_document(Path(name).name), {name})
    with pytest.raises(policy.HandoffError, match="PUBLIC_PRIVATE_DOCUMENT"):
        gate.check_source(name, b"SYNTHETIC UNREVIEWED QUESTIONS", {name})


@pytest.mark.parametrize(
    "payload", ["hidden", "defined_name", "metadata", "external", "shared", "comment"]
)
def test_blank_template_ooxml_is_scanned_including_hidden_content(payload):
    data = (ROOT / gate.TEMPLATE).read_bytes()
    gate.check_template(data)
    with ZipFile(io.BytesIO(data)) as archive:
        files = {n: archive.read(n) for n in archive.namelist()}
    ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    if payload in {"hidden", "defined_name"}:
        book = ET.fromstring(files["xl/workbook.xml"])
        if payload == "hidden":
            next(book.iter(ns + "sheet")).set("state", "veryHidden")
        else:
            parent = ET.SubElement(book, ns + "definedNames")
            ET.SubElement(
                parent, ns + "definedName", {"name": "PRIVATE"}
            ).text = "SYNTHETIC PRIVATE VALUE"
        files["xl/workbook.xml"] = ET.tostring(book)
    elif payload == "metadata":
        files["docProps/core.xml"] = files["docProps/core.xml"].replace(
            b"Water Sample Ranker", b"SYNTHETIC PRIVATE AUTHOR"
        )
    else:
        name = {
            "external": "xl/externalLinks/externalLink1.xml",
            "shared": "xl/sharedStrings.xml",
            "comment": "xl/comments/comment1.xml",
        }[payload]
        files[name] = (
            f'<sst xmlns="{ns[1:-1]}"><si><t>SYNTHETIC PRIVATE CELL</t></si></sst>'.encode()
        )
    out = io.BytesIO()
    with ZipFile(out, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    with pytest.raises(policy.HandoffError):
        gate.check_template(out.getvalue())


def test_unexpected_tracked_file_cannot_bypass_gitignore(repository):
    (repository / "extra.py").write_text("SYNTHETIC = True\n")
    command(repository, "add", "extra.py")
    command(repository, "commit", "-m", "SYNTHETIC EXTRA")
    with pytest.raises(policy.HandoffError, match="ALLOWLIST"):
        gate.scan_tree(repository)


@pytest.mark.parametrize("encoding", ["ascii", "utf-16le"])
def test_unknown_binary_markers_still_fail_closed(encoding):
    from scripts.release_assets import binary_privacy

    for text in ("SENSITIVE_" + "PRODUCTION_SENTINEL", "-----BEGIN " + "PRIVATE KEY-----"):
        with pytest.raises(policy.HandoffError):
            binary_privacy(text.encode(encoding))


def test_public_license_hash_exception_rejects_any_added_operator_content():
    name = next(iter(policy.PUBLIC_LICENSES))
    raw = (ROOT / name).read_bytes()
    policy.scan_member(name, raw)
    with pytest.raises(policy.HandoffError, match="UNREVIEWED_LICENSE_BYTES"):
        policy.scan_member(name, raw + b"\nSYNTHETIC PRIVATE ADDITION")


def test_untracked_python_cannot_enter_isolated_build_context(tmp_path):
    import shutil

    from water_sample_ranker.build_provenance import build_inputs

    approved = build_inputs(ROOT)
    for name in approved:
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    leak = tmp_path / "src/water_sample_ranker/untracked_private.py"
    leak.write_text("SENSITIVE_" + "PRODUCTION_SENTINEL", encoding="utf-8")
    assert build_inputs(tmp_path) == approved
