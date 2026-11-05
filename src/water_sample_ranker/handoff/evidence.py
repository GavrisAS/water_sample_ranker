"""Свежие проверки и строгие агрегатные проекции, без строк скважин."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import time
import tomllib
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from zipfile import ZipFile

from scripts import verify_handoff as policy

FIELDS = {"береговое", "пырейное", "хадырьяхинское"}
ROLES = {
    "calculator",
    "fund",
    "lha",
    "well_history",
    "gdi",
    "daily",
    "monitoring",
    "tech_mode",
    "gtm_plan",
    "geodata",
}


def stamp(path: Path) -> dict[str, Any]:
    stat = path.stat()
    with path.open("rb") as stream:
        digest = __import__("hashlib").file_digest(stream, "sha256").hexdigest()
    after = path.stat()
    if (stat.st_size, stat.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise policy.HandoffError("STALE_FILE")
    return {"sha256": digest, "bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def input_inventory(root: Path) -> tuple[dict[str, Path], dict[str, Any]]:
    """Без discover/load_config: по умолчанию не читаем ячейки production."""
    config = root / "config.toml"
    raw = tomllib.loads(config.read_text(encoding="utf-8")) if config.is_file() else {}
    paths = {}
    for role, name in raw.get("paths", {}).items():
        if role not in ROLES or not isinstance(name, str) or not name:
            continue
        path = Path(name)
        paths[role] = path if path.is_absolute() else root / path
    paths.setdefault("calculator", root / "templates/Water_Sample_Ranking_Calculator.xlsx")
    safe = {}
    for role, path in sorted(paths.items()):
        safe[role] = {"status": "AVAILABLE" if path.is_file() else "MISSING"}
        if path.is_file():
            safe[role].update(stamp(path))
    return paths, {
        "status": "PASS",
        "mode": "HASH_AND_CONFIG_ROLES_ONLY",
        "roles": safe,
        "row_data_parsed": False,
        "unconfigured_roles": sorted(ROLES - paths.keys()),
    }


def protected(root: Path, active: dict[str, Path]) -> dict[Path, dict[str, Any]]:
    files = {
        p
        for base in (root / "data", root / "gdis_loader", root / "templates")
        if base.is_dir()
        for p in base.rglob("*")
        if p.is_file() and not p.is_symlink()
    }
    files.update(p for p in root.glob("gdis_loader*") if p.is_file())
    files.update(p for p in root.glob("*.xls*") if p.is_file())
    files.update(p for p in active.values() if p.is_file())
    if (root / "config.toml").is_file():
        files.add(root / "config.toml")
    return {p: stamp(p) for p in sorted(files)}


def clean_log(text: str, root: Path, temporary: Path | None = None) -> str:
    for path, label in (
        (root, "<PROJECT>"),
        (temporary, "<TEMP>"),
        (Path(sys.prefix), "<ENV>"),
        (Path.home(), "<USER>"),
    ):
        if path:
            for spelling in {str(path), str(path).replace("\\", "/")}:
                text = text.replace(spelling, label)
    text = re.sub(r"/(?:tmp|var/tmp)/[^\s\"']+", "<TEMP>", text)
    text = re.sub(r"(?<!\w)[A-Za-z]:[\\/][^\s\"']+", "<ABSOLUTE_PATH>", text)
    # ANSI/terminal escape sequences не входят в evidence.
    text = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", text)
    policy.scan_text("command.txt", text.encode())
    return text


def run_gate(
    command: list[str], root: Path, timeout: float, label: str
) -> tuple[dict[str, Any], bytes]:
    started = time.monotonic()
    started_utc = datetime.now(UTC).isoformat()
    env = dict(
        os.environ,
        UV_OFFLINE="1",
        PYTHONDONTWRITEBYTECODE="1",
        QT_QPA_PLATFORM="offscreen",
        GIT_TERMINAL_PROMPT="0",
        UV_LINK_MODE="copy",
    )
    with tempfile.TemporaryDirectory(prefix="wsr-gate-") as tmp:
        output = Path(tmp) / "output.txt"
        try:
            with output.open("wb") as stream:
                process = subprocess.run(
                    command,
                    cwd=root,
                    env=env,
                    stdout=stream,
                    stderr=subprocess.STDOUT,
                    timeout=timeout,
                    check=False,
                )
            code = process.returncode
            status = "PASS" if code == 0 else "FAIL"
        except subprocess.TimeoutExpired:
            code, status = None, "FAIL"
        except OSError:
            code, status = None, "NOT_RUN"
        complete = output.exists() and output.stat().st_size <= 512 * 1024
        raw = output.read_bytes()[: 512 * 1024] if output.exists() else b""
        if not complete and status == "PASS":
            status = "PARTIAL"
        text = clean_log(raw.decode("utf-8", errors="replace"), root, Path(tmp))
        if not complete:
            text += "\n[LOG_INCOMPLETE]\n"
    safe_command = ["python" if arg == sys.executable else arg for arg in command]
    safe_command = [clean_log(arg, root) for arg in safe_command]
    data = text.encode()
    return {
        "status": status,
        "command": safe_command,
        "cwd": "<PROJECT>",
        "duration_seconds": round(time.monotonic() - started, 3),
        "started_utc": started_utc,
        "completed_utc": datetime.now(UTC).isoformat(),
        "returncode": code,
        "log_complete": complete,
        "log": f"review/logs/{label}.txt",
        "log_sha256": policy.sha(data),
    }, data


def checks(
    root: Path, enabled: bool, sync: bool, frozen: bool, timeout: float
) -> tuple[dict[str, Any], dict[str, bytes], dict[str, Any]]:
    uv = ["uv", "run", "--offline", "--no-sync"]
    commands = {
        "ruff_lint": [*uv, "ruff", "check", "."],
        "ruff_format": [*uv, "ruff", "format", "--check", "."],
        "ty": [*uv, "ty", "check"],
        "pytest": [*uv, "pytest", "-q"],
        "cli_validate_help": [
            sys.executable,
            "-B",
            "-m",
            "water_sample_ranker.cli",
            "validate",
            "--help",
        ],
        "cli_update_help": [
            sys.executable,
            "-B",
            "-m",
            "water_sample_ranker.cli",
            "update",
            "--help",
        ],
        "gui_headless": [
            sys.executable,
            "-B",
            "-c",
            "import tempfile; from pathlib import Path; from PySide6.QtWidgets import "
            "QApplication; "
            "from water_sample_ranker.config import AppConfig; "
            "from water_sample_ranker.gui.app import LoaderWindow; "
            "app=QApplication([]); tmp=tempfile.TemporaryDirectory(); "
            "window=LoaderWindow(AppConfig(Path(tmp.name))); "
            "assert not window._update_button.isEnabled(); "
            "assert window._progress_bar.maximum()==100; print('HEADLESS_WIDGETS_OK')",
        ],
    }
    gates, logs = {}, {}
    if sync:
        gate, data = run_gate(["uv", "sync", "--locked", "--offline"], root, timeout, "sync_locked")
        gates["sync_locked"], logs[gate["log"]] = gate, data
    else:
        gates["sync_locked"] = {"status": "NOT_RUN", "reason": "EXPLICIT_OPTION_REQUIRED"}
    for name, command in commands.items():
        if enabled:
            gate, data = run_gate(command, root, timeout, name)
            if (
                name == "pytest"
                and gate["status"] == "PASS"
                and not re.search(r"\d+ passed", data.decode())
            ):
                gate["status"] = "PARTIAL"
                gate["reason"] = "PYTEST_COUNT_UNKNOWN"
            gates[name], logs[gate["log"]] = gate, data
        else:
            gates[name] = {"status": "NOT_RUN", "reason": "SKIP_CHECKS_OPTION"}
    bound = frozen_binding(root) if frozen else None
    binary = (
        bound[0]
        if bound
        else (
            root
            / "dist/Water_Sample_Ranker"
            / ("Water_Sample_Ranker.exe" if os.name == "nt" else "Water_Sample_Ranker")
        )
    )
    if frozen and binary.is_file():
        gate, data = run_gate([str(binary), "--smoke-test"], root, timeout, "frozen_smoke")
        gate["binary"] = {k: v for k, v in stamp(binary).items() if k != "mtime_ns"}
        gate["source_binding"] = "VERIFIED_CODE_RESOURCE_SNAPSHOT" if bound else "UNKNOWN"
        if bound:
            gate["build_provenance"] = bound[1]
            for filename in ("build.log", "smoke.log"):
                path = binary.parents[2] / filename
                data_build = clean_log(
                    path.read_text(encoding="utf-8", errors="replace"), root
                ).encode()
                logs["review/logs/fresh_" + filename.removesuffix(".log") + ".txt"] = data_build
        if gate["status"] == "PASS" and not bound:
            gate["status"] = "PARTIAL"
            gate["reason"] = "SOURCE_BINDING_UNKNOWN; FRESH_BUILD_PENDING"
        gate["scope"] = (
            "FRESH_BUILD_IMPORTS_TEMPLATE_ONLY"
            if bound
            else "EXISTING_BINARY_IMPORTS_TEMPLATE_ONLY"
        )
        gates["frozen_smoke"], logs[gate["log"]] = gate, data
    else:
        gates["frozen_smoke"] = {"status": "NOT_RUN", "reason": "OPTION_OR_BINARY_MISSING"}
    for name in ("windows_native", "excel_native", "gui_visual_review"):
        gates[name] = {"status": "NOT_RUN", "reason": "HUMAN_GATE_PENDING"}
    versions: dict[str, Any] = {"python": sys.version.split()[0]}
    for name in ("uv", "ruff", "ty", "pytest"):
        if name == "uv":
            gate, data = run_gate(["uv", "--version"], root, timeout, "version_uv")
        else:
            gate, data = run_gate([*uv, name, "--version"], root, timeout, "version_" + name)
        gates["version_" + name], logs[gate["log"]] = gate, data
        versions[name] = {"status": gate["status"], "output": data.decode().strip()}
    return gates, logs, versions


def frozen_binding(root: Path) -> tuple[Path, dict[str, Any]] | None:
    """Исторический smoke не получает source-bound PASS; receipt проверяется заново."""
    from water_sample_ranker.build_provenance import build_inputs, content_digest

    try:
        pointer = json.loads((root / "artifacts/private/frozen-current.json").read_text())
        receipt_path = (root / pointer["receipt"]).resolve()
        if not receipt_path.is_relative_to((root / "artifacts/private").resolve()):
            return None
        receipt = json.loads(receipt_path.read_text())
        inputs = build_inputs(root)
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
        binary = (root / receipt["binary"]).resolve()
        if (
            receipt["status"] != "PASS"
            or receipt["inputs"] != inputs
            or receipt["code_resource_fingerprint"] != content_digest(inputs)
            or receipt["source_commit"] != commit
            or not binary.is_relative_to(receipt_path.parent)
            or fingerprint_for_build(binary) != receipt["binary_fingerprint"]
        ):
            return None
        if any(
            stamp(receipt_path.parent / filename)["sha256"] != receipt[key]
            for filename, key in (
                ("build.log", "build_log_sha256"),
                ("smoke.log", "smoke_log_sha256"),
            )
        ):
            return None
        template = binary.parent / "templates/Water_Sample_Ranking_Calculator.xlsx"
        if stamp(template)["sha256"] != inputs["templates/Water_Sample_Ranking_Calculator.xlsx"]:
            return None
        return binary, {
            k: receipt[k]
            for k in (
                "status",
                "scope",
                "source_commit",
                "code_resource_fingerprint",
                "inputs",
                "started_utc",
                "completed_utc",
                "platform",
                "python",
                "build_log_sha256",
                "smoke_log_sha256",
            )
        }
    except OSError, ValueError, KeyError, subprocess.SubprocessError:
        return None


def fingerprint_for_build(path: Path) -> dict[str, Any]:
    from water_sample_ranker.source_manifest import fingerprint

    return fingerprint(path)


def excel_inventory(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {
            "status": "NOT_RUN",
            "reason": "TEMPLATE_MISSING",
            "binary": "TEMPLATE_BINARY_EXCLUDED",
        }
    try:
        from water_sample_ranker.excel.compatibility import inspect_template

        inventory = inspect_template(path)
        ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
        with ZipFile(path) as z:
            workbook = ET.fromstring(z.read("xl/workbook.xml"))
            sheets = [
                {"name": s.get("name"), "visibility": s.get("state", "visible")}
                for s in workbook.iter(ns + "sheet")
            ]
            counts = []
            for name in sorted(z.namelist()):
                if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", name):
                    x = ET.fromstring(z.read(name))
                    counts.append(
                        {
                            "formulas": sum(1 for _ in x.iter(ns + "f")),
                            "protection": x.find(ns + "sheetProtection") is not None,
                            "conditional_formats": sum(
                                1 for _ in x.iter(ns + "conditionalFormatting")
                            ),
                        }
                    )
        return {
            "status": "PASS",
            "fingerprint": stamp(path),
            "structure": inventory,
            "sheet_metadata": sheets,
            "sheet_statistics": counts,
            "raw_cell_values_included": False,
            "native_excel": "NOT_RUN",
            "binary": "TEMPLATE_BINARY_EXCLUDED",
        }
    except OSError, ValueError, KeyError, ET.ParseError:
        return {
            "status": "FAIL",
            "reason": "TEMPLATE_STRUCTURAL_FAILURE",
            "binary": "TEMPLATE_BINARY_EXCLUDED",
        }


def historical(root: Path) -> dict[str, Any]:
    path = root / "artifacts/private/delivery/qualification.json"
    if not path.is_file():
        return {"status": "NOT_RUN", "historical_only": True}
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {
        "status": "UNKNOWN",
        "historical_only": True,
        "fingerprint": stamp(path),
        "age_seconds": max(0, round(time.time() - path.stat().st_mtime)),
        "calculation_date": raw.get("calculation_date"),
        "field_counts": {
            f: x.get("rows")
            for f, x in raw.get("fields", {}).items()
            if f in FIELDS and type(x.get("rows")) is int and x["rows"] >= 0
        },
        "fresh_execution": False,
    }


def aggregate(result: Any) -> dict[str, Any]:
    rows = result.rows
    fields = {}
    for field in sorted({r.identity.fund.normalized_field for r in rows}):
        if field not in FIELDS:
            raise policy.HandoffError("UNKNOWN_FIELD_LABEL")
        selected = [r for r in rows if r.identity.fund.normalized_field == field]
        identities = [
            (
                r.identity.fund.base_well_id,
                r.identity.display,
                r.identity.stage.stage_sequence if r.identity.stage else None,
                r.identity.plast,
            )
            for r in selected
        ]
        states = Counter(r.identity.status for r in selected)
        fields[field] = {
            "rows": len(selected),
            "matching": {
                "MATCHED": states["RESOLVED"],
                "AMBIGUOUS": states["AMBIGUOUS"],
                "UNMATCHED": states["UNRESOLVED"],
            },
            "duplicate_identities": len(identities) - len(set(identities)),
            "lifecycle_events": dict(
                Counter(r.identity.stage.event_kind.value for r in selected if r.identity.stage)
            ),
            "categories": dict(Counter(r.identity.fund.source_category.value for r in selected)),
            "criteria": {
                str(n): {
                    state: sum(r.criteria[n - 1].state.value == state for r in selected)
                    for state in ("TRUE", "FALSE", "UNKNOWN", "NOT_APPLICABLE")
                }
                for n in range(1, 10)
            },
            "mean_coverage": sum(r.coverage or 0 for r in selected) / len(selected),
            "unknown_fraction": sum(r.unknown_fraction for r in selected) / len(selected),
            "pair_conflicts": sum(r.pair.conflict for r in selected),
            "last_sample_count": sum(r.pair.last is not None for r in selected),
            "numeric_scores": sum(
                c.normalized_score is not None for r in selected for c in r.criteria
            ),
        }
    codes = Counter(
        i["code"] if re.fullmatch("[A-Z_]{3,80}", i["code"]) else "OTHER"
        for i in result.report.get("issues", [])
    )
    reconciliation = result.report.get("fund_reconciliation", {})
    return {
        "fields": fields,
        "diagnostic_counts": dict(codes),
        "fund_reconciliation": {
            k: reconciliation.get(k)
            for k in (
                "parsed_lists",
                "detail_scope_C_D_G_H",
                "grand_C_D_I",
                "detail_K",
                "known_mismatch_observed",
            )
        },
        "calculation_date": result.report.get("calculation_date"),
        "fund_date": result.report.get("fund_date"),
        "last_sample_date": result.report.get("lha", {}).get("last_date"),
        "lha_metrics": {
            k: result.report.get("lha", {}).get(k)
            for k in (
                "records",
                "invalid_dates",
                "future_records",
                "cache_status",
                "invalid_measurements",
            )
        },
        "source_freshness": result.report.get("source_freshness"),
        "source_changed_count": result.report.get("source_changed_count"),
        "evidence_stage_b": result.report.get("evidence_stage_b"),
        "monitoring_aggregate": {
            "choke_rows": result.report.get("monitoring", {}).get("choke_rows"),
            "observations": result.report.get("monitoring", {}).get("observations"),
            "mass_zero_dates": [
                {"date": g["date"], "count": g["count"], "total": g["total"], "share": g["share"]}
                for g in result.report.get("monitoring", {}).get("suspicion_groups", [])
                if g["all_rows_zero"]
            ],
        },
    }


def production(root: Path, enabled: bool) -> dict[str, Any]:
    if not enabled:
        return {
            "status": "NOT_RUN",
            "reason": "EXPLICIT_PRODUCTION_OPTION_REQUIRED",
            "fresh_execution": False,
        }
    from water_sample_ranker.config import load_config
    from water_sample_ranker.service import ServiceResult, run_update_all

    started = time.monotonic()
    try:
        config = load_config(root)
        day = date.today()
        updates = {}
        with tempfile.TemporaryDirectory(prefix="wsr-production-") as temp:
            results = run_update_all(config, as_of=day, output_dir=Path(temp))
            checked = ServiceResult(
                results[0].report, tuple(r for result in results for r in result.rows)
            )
            summary = aggregate(checked)
            if any(
                f["numeric_scores"] or f["duplicate_identities"] for f in summary["fields"].values()
            ):
                raise policy.HandoffError("SCORE_OR_IDENTITY_INVARIANT")
            for result in results:
                field = result.report["selected_field"]
                if field not in FIELDS:
                    raise policy.HandoffError("UNKNOWN_FIELD_LABEL")
                assert result.output is not None
                physical = verify_output_aggregates(result.output, summary, field)
                if stamp(result.output)["sha256"] != result.report["output"]["sha256"]:
                    raise policy.HandoffError("OUTPUT_HASH_MISMATCH")
                updates[field] = {
                    "independent_output_read": physical,
                    "status": "PASS",
                    "rows_written": result.report["rows_written"],
                    "output_sha256": result.report["output"]["sha256"],
                    "structure": result.report["output_structure"],
                    "changed_parts": result.report["changed_ooxml_parts"],
                }
        return {
            "status": "PASS",
            "fresh_execution": True,
            "scope": "AUTOMATED_FACTS_ONLY",
            "duration_seconds": round(time.monotonic() - started, 3),
            "summary": summary,
            "source_fingerprints": {
                role: {
                    "sha256": entry["sha256"],
                    "bytes": entry["size"],
                    "mtime_ns": entry["mtime_ns"],
                }
                for role, entry in checked.report["source_manifest"].items()
                if role in ROLES
            },
            "updates": updates,
            "private_temporary_results_deleted": True,
            "rating_methodology": "UNKNOWN",
        }
    except policy.HandoffError:
        raise
    except Exception:
        # Никаких traceback/result.summary(): они содержат private paths и identities.
        return {"status": "FAIL", "reason": "PRODUCTION_EXECUTION_FAILED", "fresh_execution": True}


def verify_output_aggregates(path: Path, expected: dict[str, Any], field: str) -> dict[str, Any]:
    """Повторное stdlib-чтение записанной книги, без выгрузки значений/номеров."""
    ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    with ZipFile(path) as z:
        root = ET.fromstring(z.read("xl/worksheets/sheet3.xml"))
        header = next(r for r in root.iter(ns + "row") if r.get("r") == "6")
        columns = {
            "".join(t.text or "" for t in c.iter(ns + "t")): re.sub(r"\d+$", "", c.get("r", ""))
            for c in header
        }
        counts = {str(i): Counter() for i in range(1, 10)}
        actual_rows = 0
        for row in root.iter(ns + "row"):
            if int(row.attrib["r"]) < 7:
                continue
            actual_rows += 1
            cells = {re.sub(r"\d+$", "", c.get("r", "")): c for c in row}
            for i in range(1, 10):
                cell = cells[columns[f"Критерий {i}"]]
                state = "".join(t.text or "" for t in cell.iter(ns + "t"))
                if state not in {"TRUE", "FALSE", "UNKNOWN", "NOT_APPLICABLE"}:
                    raise policy.HandoffError("OUTPUT_STATE_INVALID")
                counts[str(i)][state] += 1
        if actual_rows != expected["fields"][field]["rows"]:
            raise policy.HandoffError("OUTPUT_ROW_COUNT_MISMATCH")
        for i, counter in counts.items():
            if dict(counter) != {
                k: v for k, v in expected["fields"][field]["criteria"][i].items() if v
            }:
                raise policy.HandoffError("OUTPUT_CRITERION_MISMATCH")
        for sheet, cols in ((5, {"J", "K"}), (6, {"A", "E"})):
            xml = ET.fromstring(z.read(f"xl/worksheets/sheet{sheet}.xml"))
            for cell in xml.iter(ns + "c"):
                coord = cell.get("r", "")
                match = re.fullmatch(r"([A-Z]+)(\d+)", coord)
                if match and match.group(1) in cols and int(match.group(2)) >= 4:
                    formula = cell.find(ns + "f")
                    if formula is None or formula.text != '""':
                        raise policy.HandoffError("UNAPPROVED_OUTPUT_SCORE")
    return {
        "status": "PASS",
        "physical_rows": actual_rows,
        "criterion_counts_match": True,
        "score_rank_blank_formulas": True,
        "scope": "STRUCTURE_NOT_EXCEL_RECALCULATION",
    }
