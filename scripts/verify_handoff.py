"""Автономная проверка handoff: только Python stdlib, без исходного дерева и сети."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import lzma
import re
import stat
import tarfile
import tempfile
import unicodedata
import xml.etree.ElementTree as ET
import zlib
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any
from zipfile import ZipFile

SCHEMA = 1
MAX_FILES = 2048
MAX_FILE = 8 * 1024**2
MAX_TOTAL = 64 * 1024**2
MAX_ARCHIVE = 32 * 1024**2
STATUSES = {"PASS", "FAIL", "NOT_RUN", "PARTIAL", "BLOCKED", "UNKNOWN"}
# Public upstream copyright/license texts, independently downloaded and hashed.
# Exact bytes only: this exception cannot admit operator contacts or private data.
PUBLIC_LICENSES = {
    "docs/licenses/" + name: digest
    for name, digest in (
        ("PyInstaller.txt", "0598064c7d2718e38d7914a7d08343b2fa008e3bea9ebba2aa7a6ffa5900dd64"),
        ("Python.txt", "b0e25a78cffb43f4d92de8b61ccfa1f1f98ecbc22330b54b5251e7b6ba010231"),
        ("Qt-GPL-3.0.txt", "8ceb4b9ee5adedde47b31e975c1d90c73ad27b6b165a1dcd80c7c545eb65b903"),
        ("Qt-LGPL-3.0.txt", "da7eabb7bafdf7d3ae5e9f223aa5bdc1eece45ac569dc21b3b037520b4464768"),
        ("et-xmlfile.txt", "4ccdaaebc0f44b83720ec0399bb7ab329adce067dd62f73c5535a2dc05628ab2"),
        ("openpyxl.txt", "0c84bb42f5d367e5ebf9fc2dde35b16141df5ee0fdc189250858bc6c5560f69e"),
        ("tomli-w.txt", "b80816b0d530b8accb4c2211783790984a6e3b61922c2b5ee92f3372ab2742fe"),
    )
}
DOCUMENTS = {
    "ARCHITECTURE.md",
    "ACCEPTANCE.md",
    "DATA_MAPPING.md",
    "OPEN_QUESTIONS.md",
    "UX_PARITY_GDIS.md",
    "AUDIT_HANDOFF.md",
    "ATTRIBUTION.md",
    "STAGE_A1_REPORT.md",
    "STAGE_B_BASELINE.md",
    "STAGE_B_REPORT.md",
    "CUSTOMER_DECISIONS_STAGE_B.md",
    "WINDOWS_EXCEL_ACCEPTANCE.md",
    "OPTIONAL_INPUTS.md",
    "EXCEL_TEMPLATE_AUDIT.md",
    "RELEASE_PROCESS.md",
    "RELEASE_NOTES.md",
    "STAGE_B1_REPORT.md",
    "THIRD_PARTY_NOTICES.txt",
    "THIRD_PARTY_LICENSES.json",
    "PUBLIC_BINARY_REVIEW.json",
}
ROOT_FILES = {
    "README.md",
    "AGENTS.md",
    "pyproject.toml",
    "uv.lock",
    "config.example.toml",
    ".gitignore",
    ".gitattributes",
    ".python-version",
    "Makefile",
    ".editorconfig",
}
REQUIRED = {
    "audit": {
        "audit/CONTEXT.json",
        "audit/SOURCE_MANIFEST.json",
        "audit/README.md",
        "audit/pyproject.toml",
        "audit/uv.lock",
        "audit/scripts/verify_handoff.py",
        "audit/src/water_sample_ranker/service.py",
        "audit/tests/test_water_rules.py",
    },
    "review": {
        "review/CONTEXT.json",
        "review/REPORT.md",
        "review/CODE_MAP.md",
        "review/verify_handoff.py",
        "review/reports/DOMAIN_AUDIT.md",
        "review/reports/gates.json",
        "review/reports/coverage.json",
        "review/reports/source_inventory.json",
        "review/reports/excel_structure.json",
        "review/reports/privacy.json",
        "review/reports/preservation.json",
    },
}
DENIED_PARTS = {
    "data",
    ".git",
    ".venv",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    "private",
    "dist",
    "build",
    "models",
    "credentials",
    "secrets",
}
TEXT_EXT = {".py", ".md", ".json", ".toml", ".lock", ".txt", ".yml", ".yaml", ".ps1", ".patch"}
PERSON = re.compile(
    r"\b(?:Александр|Артур|Иван|Пётр|Петр|Сергей|Андрей|Мария|Анна)\s+[А-ЯЁ][а-яё]{2,}\b"
)
PATTERNS = {
    "OPERATOR_PATH": re.compile(
        r"/(?:home|Users|data/projects|tmp|var/tmp)/[^\s\"'<>]+|(?<!\w)[A-Za-z]:[\\/][^\s\"'<>]+"
    ),
    "NETWORK_PATH": re.compile(r"(?<!\\)\\\\[A-Za-z0-9][^\s\"']+"),
    "EMAIL": re.compile(r"\b[A-Za-z0-9_.+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
    "PRIVATE_KEY": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "TOKEN": re.compile(r"\b(?:sk-|ghp_|github_pat_)[A-Za-z0-9_-]{20,}\b"),
    "CREDENTIAL": re.compile(
        r"(?i)\b(?:api[_-]?key|access[_-]?token|password|secret)\s*[:=]\s*[\"']([A-Za-z0-9_+/.=-]{12,})[\"']"
    ),
}
FORBIDDEN_JSON_KEYS = {
    "well_id",
    "raw_display",
    "raw_value",
    "raw_block",
    "source_locations",
    "field_provenance",
    "raw_well_name",
    "well_cell",
    "source_row",
}


class HandoffError(ValueError):
    """Код категории ошибки; никаких секретных значений в сообщении."""


def canonical(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n"
    ).encode()


def limited_read(path: Path, limit: int = MAX_FILE) -> bytes:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > limit:
        raise HandoffError("FILE_TYPE_OR_SIZE")
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise HandoffError("FILE_SIZE")
    return data


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sealed(value: dict[str, Any]) -> bytes:
    payload = {k: v for k, v in value.items() if k != "integrity_sha256"}
    return canonical(payload | {"integrity_sha256": sha(canonical(payload))})


def parse_json(data: bytes, seal: bool = False) -> dict[str, Any]:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result = {}
        for k, v in items:
            if k in result:
                raise HandoffError("JSON_DUPLICATE_KEY")
            result[k] = v
        return result

    try:
        value = json.loads(
            data,
            object_pairs_hook=pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(HandoffError("JSON_NUMBER")),
        )
        if not isinstance(value, dict) or canonical(value) != data:
            raise HandoffError("NONCANONICAL_JSON")
        if seal and sealed(value) != data:
            raise HandoffError("JSON_INTEGRITY")
        return value
    except (UnicodeError, json.JSONDecodeError, TypeError) as error:
        raise HandoffError("INVALID_JSON") from error


def safe_path(name: str) -> str:
    if not isinstance(name, str) or not name or len(name) > 240:
        raise HandoffError("PATH_LENGTH")
    if (
        "\\" in name
        or ":" in name
        or name.startswith("/")
        or any(unicodedata.category(c).startswith("C") for c in name)
    ):
        raise HandoffError("UNSAFE_PATH")
    parts = name.split("/")
    for part in parts:
        stem = part.split(".")[0].casefold()
        if part in {"", ".", ".."} or part != part.rstrip(" .") or len(part) > 120:
            raise HandoffError("UNSAFE_PATH")
        if stem in {
            "con",
            "prn",
            "aux",
            "nul",
            *(f"com{i}" for i in range(10)),
            *(f"lpt{i}" for i in range(10)),
        }:
            raise HandoffError("WINDOWS_RESERVED_PATH")
    normalized = unicodedata.normalize("NFKC", name)
    if normalized != name:
        safe_path(normalized)
    return normalized.casefold()


def allowed_source(name: str, binary: bool = False) -> bool:
    safe_path(name)
    p = PurePosixPath(name)
    lowered = [s.casefold() for s in p.parts]
    if any(s in DENIED_PARTS or s.startswith((".env", "gdis_loader")) for s in lowered):
        return False
    if any(s in p.name.casefold() for s in ("credential", "id_rsa", "id_ed25519")):
        return False
    return (
        name in ROOT_FILES
        or name in {"evidence/WORKTREE.patch", "evidence/STAGED.patch"}
        or (name.startswith("src/water_sample_ranker/") and p.suffix == ".py")
        or (name.startswith("tests/") and p.suffix in {".py", ".txt"})
        or (name.startswith("scripts/") and p.suffix in {".py", ".ps1"})
        or name == "scripts/public_files.txt"
        or name == "docs/README_ПЕРВЫЙ_ЗАПУСК.txt"
        or name in PUBLIC_LICENSES
        or (name.startswith(".github/workflows/") and p.suffix in {".yml", ".yaml"})
        or (len(p.parts) == 2 and p.parts[0] == "docs" and p.name in DOCUMENTS)
        or (binary and name == "templates/Water_Sample_Ranking_Calculator.xlsx")
    )


def check_template_binding(
    included: bool, present: bool, report: dict[str, Any], gate: dict[str, Any]
) -> None:
    expected = "SCANNED_BLANK_TEMPLATE_INCLUDED" if included else "TEMPLATE_BINARY_EXCLUDED"
    if (
        included != present
        or report.get("binary") != expected
        or gate.get("status") != ("PASS" if included else "PARTIAL")
        or gate.get("reason") != ("SCANNED_BLANK_TEMPLATE" if included else expected)
    ):
        raise HandoffError("TEMPLATE_BINARY_CONTEXT")


def check_build_binding(
    gate: dict[str, Any], entries: dict[str, Any], head: str | None, template_sha: str | None = None
) -> None:
    if gate.get("source_binding") != "VERIFIED_CODE_RESOURCE_SNAPSHOT":
        return
    provenance = gate.get("build_provenance", {})
    inputs = provenance.get("inputs", {})
    if (
        not inputs
        or provenance.get("source_commit") != head
        or provenance.get("status") != "PASS"
        or provenance.get("code_resource_fingerprint")
        != sha(json.dumps(inputs, sort_keys=True).encode())
        or any(
            (
                template_sha
                if name == "templates/Water_Sample_Ranking_Calculator.xlsx" and name not in entries
                else entries.get(name, {}).get("original_sha256")
            )
            != digest
            for name, digest in inputs.items()
        )
    ):
        raise HandoffError("FROZEN_SOURCE_BINDING")


def check_public_release(data: bytes, head: str | None) -> dict[str, Any]:
    """Static downloaded-publication evidence; no network or invented human PASS."""
    scan_text("public_release.json", data)
    value = parse_json(data)
    required = {
        "schema",
        "status",
        "repository",
        "source_commit",
        "tag",
        "run_id",
        "run_number",
        "workflow_url",
        "workflow_status",
        "workflow_conclusion",
        "release_url",
        "isPrerelease",
        "jobs",
        "assets",
        "download_verification",
        "version",
        "binary_sha256",
        "source_fingerprint",
        "human_gates",
    }
    if set(value) != required:
        raise HandoffError("PUBLIC_RELEASE_SCHEMA")
    repo, run_id, tag = value["repository"], value["run_id"], value["tag"]
    match = re.fullmatch(r"v0\.1\.([1-9][0-9]*)", tag)
    if (
        not re.fullmatch(r"[A-Za-z0-9_.-]+/water_sample_ranker", repo)
        or not re.fullmatch(r"[0-9]+", run_id)
        or not match
        or value["schema"] != 1
        or value["status"] != "PUBLIC_PRERELEASE_PUBLISHED"
        or value["source_commit"] != head
        or not re.fullmatch(r"[a-f0-9]{40}", head or "")
        or value["run_number"] != int(match[1])
        or value["version"] != tag[1:]
        or value["workflow_url"] != f"https://github.com/{repo}/actions/runs/{run_id}"
        or value["release_url"] != f"https://github.com/{repo}/releases/tag/{tag}"
        or value["workflow_status"] != "completed"
        or value["workflow_conclusion"] != "success"
        or value["isPrerelease"] is not True
        or value["download_verification"] != "PASS"
        or value["human_gates"] != {"native_excel": "NOT_RUN", "windows_ux": "NOT_RUN"}
        or any(
            not re.fullmatch(r"[a-f0-9]{64}", value[k])
            for k in ("binary_sha256", "source_fingerprint")
        )
    ):
        raise HandoffError("PUBLIC_RELEASE_BINDING")
    expected = {
        f"Water_Sample_Ranker-{tag}-windows-x64.zip",
        f"Water_Sample_Ranker-{tag}-release-evidence.zip",
    }
    expected |= {n + ".sha256" for n in expected}
    assets = value["assets"]
    if (
        len(assets) != 4
        or {a["name"] for a in assets} != expected
        or any(
            set(a) != {"name", "sha256", "bytes"}
            or not re.fullmatch(r"[a-f0-9]{64}", a["sha256"])
            or type(a["bytes"]) is not int
            or a["bytes"] <= 0
            for a in assets
        )
    ):
        raise HandoffError("PUBLIC_RELEASE_ASSETS")
    jobs = value["jobs"]
    if not jobs or any(
        set(j) != {"name", "status", "conclusion"}
        or j["status"] != "completed"
        or j["conclusion"] != "success"
        for j in jobs
    ):
        raise HandoffError("PUBLIC_RELEASE_JOBS")
    if not any("linux-checks" in j["name"] for j in jobs) or not any(
        "windows-build" in j["name"] for j in jobs
    ):
        raise HandoffError("PUBLIC_RELEASE_JOBS")
    return value


def scan_text(name: str, data: bytes) -> None:
    try:
        text = data.decode("utf-8")
    except UnicodeError as error:
        raise HandoffError("UNEXPECTED_BINARY") from error
    for category, regex in PATTERNS.items():
        if regex.search(text):
            raise HandoffError("CONTENT_" + category)
    if PERSON.search(text):
        raise HandoffError("CONTENT_PERSONAL_NAME")
    if name.endswith(".json"):
        value = json.loads(text)

        def walk(node: Any) -> None:
            if isinstance(node, dict):
                if FORBIDDEN_JSON_KEYS & node.keys():
                    raise HandoffError("CONTENT_ROW_IDENTIFIERS")
                if isinstance(node.get("rows"), list):
                    raise HandoffError("CONTENT_RAW_ROWS")
                for v in node.values():
                    walk(v)
            elif isinstance(node, list):
                for v in node:
                    walk(v)

        walk(value)
    elif not name.endswith((".py", ".patch")):
        if re.search(
            r"(?:well_id|raw_display|raw_block|source_locations|raw_well_name)[\"']?\s*[:=]", text
        ):
            raise HandoffError("CONTENT_ROW_IDENTIFIERS")
        for match in re.finditer(r"(?i)(?:скважин[аы]|скв\.|well_id)\s*(?:№|:|=)?\s*(\d+)", text):
            if match.group(1) != "23":
                raise HandoffError("CONTENT_WELL_IDENTIFIER")


def scan_xlsx(data: bytes) -> None:
    """Разрешён только пустой source template, без произвольных ZIP payloads."""
    ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    try:
        with ZipFile(io.BytesIO(data)) as z:
            if z.comment:
                raise HandoffError("XLSX_ZIP_COMMENT")
            seen = set()
            total = 0
            for info in z.infolist():
                if info.comment or info.extra:
                    raise HandoffError("XLSX_EXTRA_METADATA")
                key = safe_path(info.filename)
                if (
                    key in seen
                    or info.is_dir()
                    or info.file_size > 32 * 1024**2
                    or stat.S_ISLNK(info.external_attr >> 16)
                ):
                    raise HandoffError("XLSX_MEMBER")
                seen.add(key)
                total += info.file_size
                if total > 96 * 1024**2 or len(seen) > 4096:
                    raise HandoffError("XLSX_LIMIT")
                if not info.filename.endswith((".xml", ".rels")) or any(
                    token in key
                    for token in (
                        "externallink",
                        "vbaproject",
                        "embedding",
                        "comments",
                        "customxml",
                        "oleobject",
                    )
                ):
                    raise HandoffError("XLSX_PAYLOAD")
                payload = z.read(info)
                scan_text(info.filename, payload)
                if b"<!DOCTYPE" in payload or b"<!ENTITY" in payload:
                    raise HandoffError("XLSX_ENTITY")
                root = ET.fromstring(payload)
                if info.filename == "xl/sharedStrings.xml" and list(root):
                    raise HandoffError("XLSX_SHARED_STRINGS_UNPROVEN")
                sheet_match = re.fullmatch(r"xl/worksheets/sheet(\d+)\.xml", info.filename)
                if sheet_match and int(sheet_match.group(1)) > 6:
                    raise HandoffError("XLSX_EXTRA_SHEET")
                for formula in root.iter(ns + "f"):
                    if re.search(r"\[(?:\d+|[^\]]+\.xls[xmb]?)\]", formula.text or "", re.I):
                        raise HandoffError("XLSX_EXTERNAL_FORMULA")
                    if any(
                        x in (formula.text or "").upper()
                        for x in ("WEBSERVICE", "HYPERLINK", "RTD(", "#REF!")
                    ):
                        raise HandoffError("XLSX_UNSAFE_FORMULA")
                if any(e.attrib.get("TargetMode") == "External" for e in root.iter()):
                    raise HandoffError("XLSX_EXTERNAL")
                if info.filename == "docProps/core.xml":
                    for e in root:
                        if e.tag.rsplit("}", 1)[-1] in {
                            "creator",
                            "lastModifiedBy",
                        } and e.text not in {None, "Water Sample Ranker"}:
                            raise HandoffError("XLSX_PERSONAL_METADATA")
                if info.filename == "xl/worksheets/sheet3.xml":
                    for row in root.iter(ns + "row"):
                        if int(row.attrib["r"]) >= 7 and any(
                            c.find(ns + "v") is not None
                            or c.find(ns + "is") is not None
                            or c.find(ns + "f") is not None
                            for c in row
                        ):
                            raise HandoffError("XLSX_DATA_ROWS")
                if info.filename in {
                    "xl/worksheets/sheet4.xml",
                    "xl/worksheets/sheet5.xml",
                    "xl/worksheets/sheet6.xml",
                }:
                    boundary = 20 if "sheet4" in info.filename else 4
                    for row in root.iter(ns + "row"):
                        if int(row.attrib["r"]) >= boundary:
                            for c in row:
                                if c.find(ns + "f") is None and (
                                    c.find(ns + "v") is not None or c.find(ns + "is") is not None
                                ):
                                    # Единственная допустимая подпись helper-диаграммы.
                                    if c.get("r") != "K20" or "sheet6" not in info.filename:
                                        raise HandoffError("XLSX_HIDDEN_OR_LITERAL_ROWS")
    except (OSError, ET.ParseError, KeyError, ValueError) as error:
        if isinstance(error, HandoffError):
            raise
        raise HandoffError("XLSX_INVALID") from error


def scan_member(name: str, data: bytes) -> None:
    source_name = name.removeprefix("audit/")
    if source_name in PUBLIC_LICENSES:
        if sha(data) != PUBLIC_LICENSES[source_name]:
            raise HandoffError("UNREVIEWED_LICENSE_BYTES")
        return
    if name.endswith(".xlsx"):
        scan_xlsx(data)
    else:
        scan_text(name, data)


def read_archive(
    path: Path, kind: str, destination: Path, raw: bytes | None = None
) -> dict[str, bytes]:
    raw = limited_read(path, MAX_ARCHIVE) if raw is None else raw
    if len(raw) > MAX_ARCHIVE:
        raise HandoffError("ARCHIVE_SIZE")
    try:
        limit = MAX_TOTAL + MAX_FILES * 1024
        if kind == "audit":
            if raw[:3] != b"\x1f\x8b\x08" or len(raw) < 10 or raw[3] not in {0, 8}:
                raise HandoffError("GZIP_METADATA")
            if raw[3] == 8:
                end = raw.find(b"\0", 10, 1024)
                if end < 0 or raw[10:end] != path.name.removesuffix(".gz").encode():
                    raise HandoffError("GZIP_FILENAME")
            decoder = zlib.decompressobj(31)
            unpacked = decoder.decompress(raw, limit + 1)
        else:
            decoder = lzma.LZMADecompressor(memlimit=64 * 1024**2)
            unpacked = decoder.decompress(raw, max_length=limit + 1)
        if len(unpacked) > limit or not decoder.eof or decoder.unused_data:
            raise HandoffError("EXPANSION_LIMIT_OR_TRAILING_DATA")
        payloads = {}
        seen = set()
        total = 0
        next_offset = 0
        with tarfile.open(fileobj=io.BytesIO(unpacked), mode="r:") as archive:
            for member in archive:
                if member.offset != next_offset or member.offset_data != next_offset + 512:
                    raise HandoffError("TAR_EXTENDED_OR_HIDDEN_HEADER")
                if unpacked[next_offset : next_offset + 512] != member.tobuf(
                    format=tarfile.USTAR_FORMAT
                ):
                    raise HandoffError("NONCANONICAL_TAR_HEADER")
                end_data = member.offset_data + member.size
                next_offset = member.offset_data + ((member.size + 511) // 512) * 512
                if any(unpacked[end_data:next_offset]):
                    raise HandoffError("TAR_HIDDEN_PADDING")
                key = safe_path(member.name)
                if key in seen or member.name.split("/")[0] != kind:
                    raise HandoffError("DUPLICATE_OR_ROOT")
                seen.add(key)
                if (
                    not member.isfile()
                    or member.issparse()
                    or member.linkname
                    or member.mode & 0o7000
                ):
                    raise HandoffError("FORBIDDEN_MEMBER_TYPE")
                if (
                    member.pax_headers
                    or member.size > MAX_FILE
                    or member.size < 0
                    or member.uid != 0
                    or member.gid != 0
                    or member.uname
                    or member.gname
                    or member.mtime != 0
                ):
                    raise HandoffError("MEMBER_LIMIT")
                if kind == "audit":
                    relative = member.name.removeprefix("audit/")
                    if relative not in {
                        "MANIFEST.json",
                        "CONTEXT.json",
                        "SOURCE_MANIFEST.json",
                    } and not allowed_source(relative, True):
                        raise HandoffError("FORBIDDEN_MEMBER")
                elif not (
                    member.name in REQUIRED["review"]
                    or member.name == "review/MANIFEST.json"
                    or member.name == "review/reports/public_release.json"
                    or re.fullmatch(r"review/logs/[a-z_]+\.txt", member.name)
                ):
                    raise HandoffError("FORBIDDEN_MEMBER")
                total += member.size
                if total > MAX_TOTAL or len(seen) > MAX_FILES:
                    raise HandoffError("TOTAL_LIMIT")
                stream = archive.extractfile(member)
                if stream is None:
                    raise HandoffError("MEMBER_READ")
                data = stream.read(MAX_FILE + 1)
                if len(data) != member.size:
                    raise HandoffError("MEMBER_TRUNCATED")
                scan_member(member.name, data)
                payloads[member.name] = data
                target = destination.joinpath(*member.name.split("/"))
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open("xb") as output:
                    output.write(data)
        if len(unpacked) - next_offset < 1024 or any(unpacked[next_offset:]):
            raise HandoffError("TAR_HIDDEN_TRAILER")
        if not REQUIRED[kind] <= payloads.keys():
            raise HandoffError("MISSING_EVIDENCE")
        return payloads
    except (tarfile.TarError, zlib.error, lzma.LZMAError, EOFError, OSError) as error:
        raise HandoffError("ARCHIVE_INVALID") from error


def check_inventory(payloads: dict[str, bytes], kind: str, manifest: dict[str, Any]) -> None:
    name = f"{kind}/MANIFEST.json"
    internal = parse_json(payloads[name])
    if (
        internal.get("handoff_id") != manifest["handoff_id"]
        or internal.get("schema_version") != SCHEMA
        or internal.get("kind") != kind
    ):
        raise HandoffError("CONTEXT_MISMATCH")
    expected = {
        n: {
            "bytes": len(data),
            "sha256": sha(data),
            "type": "xlsx" if n.endswith(".xlsx") else "text",
        }
        for n, data in payloads.items()
        if n != name
    }
    if internal.get("members") != expected or internal.get("excluded_self") != name:
        raise HandoffError("MEMBER_HASH_OR_INVENTORY")
    context = parse_json(payloads[f"{kind}/CONTEXT.json"])
    if context != {
        "handoff_id": manifest["handoff_id"],
        "schema_version": SCHEMA,
        "source": manifest["source"],
    }:
        raise HandoffError("STALE_SOURCE_SNAPSHOT")


def verify(manifest_path: Path, create_receipt: bool = False) -> dict[str, Any]:
    raw = limited_read(manifest_path)
    manifest = parse_json(raw, seal=True)
    scan_text(manifest_path.name, raw)
    expected_keys = {
        "schema_version",
        "handoff_id",
        "created_utc",
        "mode",
        "status",
        "source",
        "inputs",
        "gates",
        "artifacts",
        "required_evidence",
        "verification",
        "integrity_sha256",
    }
    if (
        set(manifest) != expected_keys
        or manifest["schema_version"] != SCHEMA
        or manifest["mode"] != "share-safe"
    ):
        raise HandoffError("SIDECAR_SCHEMA")
    hid = manifest["handoff_id"]
    if not re.fullmatch(r"\d{8}T\d{6}Z-[a-f0-9]{12}", hid) or manifest["status"] not in STATUSES:
        raise HandoffError("SIDECAR_ID_OR_STATUS")
    if manifest_path.name != f"water-sample-ranker-handoff-{hid}.json":
        raise HandoffError("SIDECAR_NAME")
    if manifest["required_evidence"] != {k: sorted(v) for k, v in REQUIRED.items()}:
        raise HandoffError("EVIDENCE_INDEX")
    counts = {}
    archives = {}
    with tempfile.TemporaryDirectory(prefix="wsr-verify-") as fresh:
        unpack = Path(fresh)
        all_payloads = {}
        for kind, suffix in (("audit", ".tar.gz"), ("review", ".tar.xz")):
            desc = manifest["artifacts"].get(kind, {})
            filename = f"water-sample-ranker-{kind}-{hid}{suffix}"
            if desc.get("path") != filename:
                raise HandoffError("ARTIFACT_PATH")
            safe_path(filename)
            archive_path = manifest_path.parent / filename
            if archive_path.is_symlink() or archive_path.stat().st_size > MAX_ARCHIVE:
                raise HandoffError("ARTIFACT_TYPE_OR_SIZE")
            data = limited_read(archive_path, MAX_ARCHIVE)
            if desc.get("sha256") != sha(data) or desc.get("bytes") != len(data):
                raise HandoffError("ARCHIVE_HASH")
            payloads = read_archive(archive_path, kind, unpack, raw=data)
            check_inventory(payloads, kind, manifest)
            if desc.get("members") != len(payloads):
                raise HandoffError("ARCHIVE_COUNT")
            all_payloads[kind] = payloads
            archives[kind] = {"sha256": sha(data), "bytes": len(data)}
            counts[kind] = len(payloads)
        source = parse_json(all_payloads["audit"]["audit/SOURCE_MANIFEST.json"])
        entries = source["files"]
        for path, entry in entries.items():
            actual = all_payloads["audit"].get("audit/" + path)
            if (
                actual is None
                or sha(actual) != entry["archived_sha256"]
                or len(actual) != entry["archived_bytes"]
            ):
                raise HandoffError("SOURCE_FILE_HASH")
        source_paths = {
            n.removeprefix("audit/")
            for n in all_payloads["audit"]
            if n not in {"audit/MANIFEST.json", "audit/CONTEXT.json", "audit/SOURCE_MANIFEST.json"}
        }
        if (
            source_paths != entries.keys()
            or sha(canonical(entries)) != manifest["source"]["fingerprint"]
        ):
            raise HandoffError("SOURCE_FINGERPRINT")
        gates = parse_json(all_payloads["review"]["review/reports/gates.json"])
        if gates != manifest["gates"]:
            raise HandoffError("GATE_CONTEXT")
        publication = all_payloads["review"].get("review/reports/public_release.json")
        if publication is not None:
            check_public_release(publication, manifest["source"]["git"]["head"])
            if gates.get("public_release", {}).get("status") != "PASS":
                raise HandoffError("PUBLIC_RELEASE_GATE_CONTEXT")
        elif gates.get("public_release", {}).get("status") == "PASS":
            raise HandoffError("PUBLIC_RELEASE_EVIDENCE_MISSING")
        check_template_binding(
            parse_json(all_payloads["review"]["review/reports/privacy.json"])[
                "template_binary_included"
            ],
            "audit/templates/Water_Sample_Ranking_Calculator.xlsx" in all_payloads["audit"],
            parse_json(all_payloads["review"]["review/reports/excel_structure.json"]),
            gates["template_binary"],
        )
        check_build_binding(
            gates.get("frozen_smoke", {}),
            entries,
            manifest["source"]["git"]["head"],
            manifest["inputs"].get("roles", {}).get("calculator", {}).get("sha256"),
        )
        for gate in gates.values():
            if gate["status"] not in STATUSES:
                raise HandoffError("GATE_STATUS")
            if (
                gate.get("command")
                and gate["status"] == "PASS"
                and (gate.get("returncode") != 0 or gate.get("log_complete") is not True)
            ):
                raise HandoffError("UNSUPPORTED_GATE_PASS")
            log = gate.get("log")
            if log and (
                log not in all_payloads["review"]
                or sha(all_payloads["review"][log]) != gate["log_sha256"]
            ):
                raise HandoffError("GATE_LOG")
        inventory_report = parse_json(
            all_payloads["review"]["review/reports/source_inventory.json"]
        )
        coverage_report = parse_json(all_payloads["review"]["review/reports/coverage.json"])
        if inventory_report != manifest["inputs"]:
            raise HandoffError("INPUT_CONTEXT")
        if coverage_report["status"] != gates["production_validation"]["status"]:
            raise HandoffError("PRODUCTION_CONTEXT")
        if coverage_report.get("source_fingerprints") and coverage_report[
            "source_fingerprints"
        ] != inventory_report.get("fresh_production_roles"):
            raise HandoffError("PRODUCTION_SOURCE_FINGERPRINTS")
        for name in ("privacy", "preservation"):
            report = parse_json(all_payloads["review"][f"review/reports/{name}.json"])
            if report.get("status") != "PASS":
                raise HandoffError("SAFETY_EVIDENCE")
            if name == "preservation" and (
                report.get("changed") != 0
                or report.get("before_fingerprint") != report.get("after_fingerprint")
            ):
                raise HandoffError("PRESERVATION_EVIDENCE")
            if name == "privacy" and report.get("raw_rows_included") is not False:
                raise HandoffError("PRIVACY_EVIDENCE")
    if limited_read(manifest_path) != raw:
        raise HandoffError("STALE_HANDOFF_MANIFEST")
    for kind, desc in manifest["artifacts"].items():
        current = limited_read(manifest_path.parent / desc["path"], MAX_ARCHIVE)
        if sha(current) != archives[kind]["sha256"]:
            raise HandoffError("STALE_HANDOFF_ARCHIVE")
    receipt_name = f"water-sample-ranker-handoff-verification-{hid}.json"
    if manifest["verification"] != receipt_name:
        raise HandoffError("RECEIPT_PATH")
    receipt_path = manifest_path.parent / receipt_name
    result = {
        "schema_version": SCHEMA,
        "handoff_id": hid,
        "status": "PASS",
        "scope": "ARCHIVE_INTEGRITY_AND_POLICY_ONLY",
        "business_correctness": "UNKNOWN",
        "source_fingerprint": manifest["source"]["fingerprint"],
        "manifest_sha256": sha(raw),
        "archives": archives,
        "members": counts,
        "fresh_extraction": True,
        "verifier_sha256": sha(Path(__file__).read_bytes()),
    }
    if create_receipt:
        if receipt_path.exists():
            raise HandoffError("RECEIPT_EXISTS")
        result["checked_utc"] = datetime.now(UTC).isoformat()
        with receipt_path.open("xb") as stream:
            stream.write(sealed(result))
    else:
        receipt_raw = limited_read(receipt_path)
        receipt = parse_json(receipt_raw, seal=True)
        scan_text(receipt_name, receipt_raw)
        checked = receipt.pop("checked_utc", None)
        receipt.pop("integrity_sha256", None)
        if not checked or receipt != result:
            raise HandoffError("RECEIPT_MISMATCH")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument(
        "--create-receipt", action="store_true", help="только первая независимая проверка"
    )
    args = parser.parse_args(argv)
    try:
        verify(args.manifest, args.create_receipt)
    except HandoffError, OSError, ValueError, KeyError, TypeError:
        print("FAIL: HANDOFF_INVALID")
        return 2
    print("PASS: ARCHIVE_INTEGRITY_PRIVACY_FRESH_EXTRACTION; BUSINESS_CORRECTNESS_UNKNOWN")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
