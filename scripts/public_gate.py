"""Fail-closed public tree/history/archive gate; stdout contains categories only."""

from __future__ import annotations

import argparse
import io
import json
import re
import stat
import subprocess
import tarfile
import xml.etree.ElementTree as ET
from pathlib import Path
from zipfile import ZipFile

from scripts import verify_handoff as policy

TEMPLATE = "templates/Water_Sample_Ranking_Calculator.xlsx"
TEMPLATE_SHA = "20ac8b94dbcb8b32ef7edac1d4eb6640531393f654e4fbe58024843449266557"
PRIVATE_DOCUMENTS = {
    "SPEC.md",
    "OPEN_QUESTIONS.md",
    "CUSTOMER_DECISIONS_STAGE_B.md",
    "SOURCE_AUDIT.md",
    "STAGE_A1_REPORT.md",
    "STAGE_B_BASELINE.md",
    "STAGE_B_REPORT.md",
    "DATA_MAPPING.md",
    "EXCEL_TEMPLATE_AUDIT.md",
    "EXCEL_STRUCTURE.json",
    "UX_PARITY_GDIS.md",
}
DENY = {"data", "private", "credentials", "secrets", ".env", ".git", ".venv"}


def public_document(name: str) -> bytes:
    if name.endswith(".json"):
        return b'{"scope":"PUBLIC_STRUCTURE_ONLY","production_evidence":"EXCLUDED"}\n'
    return (
        f"# {name.removesuffix('.md')}\n\n"
        "PUBLIC_DOCUMENT — NO PRODUCTION EVIDENCE\n\n"
        "Исходные материалы, результаты проверки производственных источников и вопросы "
        "заказчику ведутся отдельно и не публикуются. Эта публичная копия содержит "
        "только технический прототип и синтетические проверки.\n\n"
        "TECHNICAL PREVIEW — НЕ УТВЕРЖДЁННЫЙ РЕЙТИНГ. "
        "Предварительная сумма без перенормировки; UNKNOWN сохраняется. "
        "№6 ожидает правило, №9 отложен. Windows UX и native Excel требуют отдельной "
        "человеческой приёмки; CI не заменяет её. См. RELEASE_PROCESS.md и ACCEPTANCE.md.\n"
    ).encode()


def allowed_names(root: Path) -> set[str]:
    names = (root / "scripts/public_files.txt").read_text(encoding="utf-8").splitlines()
    if not names or len(names) != len(set(names)):
        raise policy.HandoffError("PUBLIC_ALLOWLIST_INVALID")
    return set(names)


def check_name(name: str) -> None:
    policy.safe_path(name)
    parts = name.casefold().split("/")
    if any(p in DENY or p.startswith(("gdis_loader", ".env")) for p in parts):
        raise policy.HandoffError("PUBLIC_DENIED_NAME")
    if any(t in name.casefold() for t in ("вопросы_заказчику", "discrepancy-register", "id_rsa")):
        raise policy.HandoffError("PUBLIC_DENIED_NAME")


def check_template(data: bytes) -> None:
    policy.scan_xlsx(data)
    if policy.sha(data) not in {
        TEMPLATE_SHA,
        "b6e54a801bd70a2ef4184e2f0ac77d56a214dc801592f00f178b43ed3cb84f66",
        "9f067b9a891640734c8f830fccddc35af5250735d875a83f05967de2cde4fc34",
    }:
        raise policy.HandoffError("PUBLIC_TEMPLATE_UNREVIEWED_BYTES")
    with ZipFile(io.BytesIO(data)) as archive:
        book = ET.fromstring(archive.read("xl/workbook.xml"))
        ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
        sheets = list(book.iter(ns + "sheet"))
        if [s.get("name") for s in sheets] != [
            "README",
            "Настройки",
            "Данные",
            "Критерии",
            "Расчет",
            "Рейтинг",
        ]:
            raise policy.HandoffError("PUBLIC_TEMPLATE_SHEETS")
        if any(s.get("state", "visible") != "visible" for s in sheets):
            raise policy.HandoffError("PUBLIC_HIDDEN_SHEET")
        for item in book.iter(ns + "definedName"):
            policy.scan_text("defined-name.txt", (item.text or "").encode())
            if "[" in (item.text or "") or "http" in (item.text or "").casefold():
                raise policy.HandoffError("PUBLIC_DEFINED_NAME")


def check_source(name: str, data: bytes, names: set[str]) -> None:
    check_name(name)
    if name not in names:
        raise policy.HandoffError("PUBLIC_NOT_ALLOWLISTED")
    if name == TEMPLATE:
        check_template(data)
        return
    if name in policy.PUBLIC_LICENSES:
        policy.scan_member(name, data)
        return
    if len(data) > 8 * 1024**2:
        raise policy.HandoffError("PUBLIC_FILE_SIZE")
    policy.scan_text(name, data)
    text = data.decode("utf-8")
    if re.search(r"\b(?:gho_|ghs_|ghu_)[A-Za-z0-9_]{20,}\b", text):
        raise policy.HandoffError("PUBLIC_TOKEN")
    if "SENSITIVE_" + "PRODUCTION_SENTINEL" in text:
        raise policy.HandoffError("PUBLIC_LEAK_CANARY")
    if name.startswith("docs/") and Path(name).name in PRIVATE_DOCUMENTS:
        current = public_document(Path(name).name)
        legacy = current.replace(
            "Предварительная сумма без перенормировки; UNKNOWN сохраняется. ".encode(),
            "Баллы, ранги и TOP ожидают утверждения методики; UNKNOWN сохраняется. ".encode(),
        ).replace(
            "№6 ожидает правило, №9 отложен. Windows UX и native Excel требуют отдельной ".encode(),
            "№7–9 — наблюдения и кандидаты. Windows UX и native Excel требуют отдельной ".encode(),
        )
        if data not in {current, legacy}:
            raise policy.HandoffError("PUBLIC_PRIVATE_DOCUMENT")
    if name.lower().endswith((".xlsx", ".xlsm", ".zip", ".tar", ".gz", ".xz")):
        raise policy.HandoffError("PUBLIC_UNREVIEWED_ARCHIVE")


def git(root: Path, *args: str) -> bytes:
    return subprocess.check_output(["git", *args], cwd=root, stderr=subprocess.DEVNULL)


def scan_tree(root: Path, *, history: bool = True) -> dict[str, object]:
    names = allowed_names(root)
    tracked = git(root, "ls-files", "-z").decode().split("\0")[:-1]
    if set(tracked) != names:
        raise policy.HandoffError("PUBLIC_TRACKED_ALLOWLIST_MISMATCH")
    for name in tracked:
        path = root / name
        if path.is_symlink() or not path.is_file():
            raise policy.HandoffError("PUBLIC_FILE_TYPE")
        check_source(name, path.read_bytes(), names)
    if git(root, "status", "--porcelain", "--untracked-files=no").strip():
        raise policy.HandoffError("PUBLIC_DIRTY_TRACKED_TREE")
    blobs: set[tuple[str, str]] = set()
    commits = git(root, "rev-list", "--all").decode().splitlines() if history else []
    # Examine every historical tree, including deleted paths and large blobs.
    # rev-list --objects inventories all reachable objects independently.
    objects = git(root, "rev-list", "--objects", "--all").splitlines() if history else []
    for commit in commits:
        raw_commit = git(root, "cat-file", "commit", commit).decode("utf-8")
        headers, message = raw_commit.split("\n\n", 1)
        for line in headers.splitlines():
            if line.startswith(("author ", "committer ")):
                match = re.fullmatch(
                    r"(?:author|committer) (.+) <([^>]+)> [0-9]+ [+-][0-9]{4}", line
                )
                if not match or not re.fullmatch(
                    r"[A-Za-z0-9_+\[\]-]+@users\.noreply\.github\.com", match[2]
                ):
                    raise policy.HandoffError("PUBLIC_COMMIT_CONTACT")
                policy.scan_text("commit-author.txt", match[1].encode())
        policy.scan_text("commit-message.txt", message.encode())
        for raw in git(root, "ls-tree", "-r", "-z", commit).split(b"\0"):
            if not raw:
                continue
            metadata, path = raw.split(b"\t", 1)
            mode, kind, oid = metadata.decode().split()
            if mode not in {"100644", "100755"} or kind != "blob":
                raise policy.HandoffError("PUBLIC_GIT_OBJECT_TYPE")
            blobs.add((oid, path.decode()))
    for oid, name in sorted(blobs):
        if int(git(root, "cat-file", "-s", oid)) > 8 * 1024**2:
            raise policy.HandoffError("PUBLIC_LARGE_HISTORY_BLOB")
        check_source(name, git(root, "cat-file", "blob", oid), names)
    return {
        "status": "PASS",
        "tracked_files": len(tracked),
        "commits": len(commits),
        "historical_blobs": len(blobs),
        "reachable_objects": len(objects),
        "template": "SCANNED_BLANK_TEMPLATE",
        "production_qualification": "NOT_INCLUDED",
    }


def scan_source_archive(path: Path, root: Path) -> dict[str, object]:
    names = allowed_names(root)
    seen: set[str] = set()
    total = 0
    with tarfile.open(path, "r:*") as archive:
        for member in archive:
            if member.isdir():
                continue
            if not member.isfile() or member.size > 8 * 1024**2:
                raise policy.HandoffError("PUBLIC_ARCHIVE_MEMBER_TYPE")
            check_name(member.name)
            if member.name.casefold() in seen:
                raise policy.HandoffError("PUBLIC_ARCHIVE_COLLISION")
            seen.add(member.name.casefold())
            stream = archive.extractfile(member)
            if stream is None:
                raise policy.HandoffError("PUBLIC_ARCHIVE_READ")
            data = stream.read(8 * 1024**2 + 1)
            total += len(data)
            if total > 64 * 1024**2:
                raise policy.HandoffError("PUBLIC_ARCHIVE_LIMIT")
            check_source(member.name, data, names)
    return {"status": "PASS", "members": len(seen)}


def checked_zip_members(path: Path) -> dict[str, bytes]:
    """Read every member, never extractall; bounded, no links or duplicate names."""
    payloads: dict[str, bytes] = {}
    total = 0
    with ZipFile(path) as archive:
        if archive.comment:
            raise policy.HandoffError("PUBLIC_ARCHIVE_COMMENT")
        seen = set()
        for info in archive.infolist():
            if info.is_dir():
                continue
            key = policy.safe_path(info.filename)
            check_name(info.filename)
            if key in seen or stat.S_ISLNK(info.external_attr >> 16) or info.comment or info.extra:
                raise policy.HandoffError("PUBLIC_ARCHIVE_MEMBER")
            seen.add(key)
            total += info.file_size
            if info.file_size > 256 * 1024**2 or total > 1024**3 or len(seen) > 4096:
                raise policy.HandoffError("PUBLIC_ARCHIVE_LIMIT")
            payloads[info.filename] = archive.read(info)
    return payloads


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", type=Path, default=Path.cwd())
    p.add_argument("--archive", type=Path)
    args = p.parse_args()
    try:
        result = (
            scan_source_archive(args.archive, args.root) if args.archive else scan_tree(args.root)
        )
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        code = str(error) if isinstance(error, policy.HandoffError) else type(error).__name__
        print(json.dumps({"status": "BLOCKED", "category": code}))
        return 2
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
