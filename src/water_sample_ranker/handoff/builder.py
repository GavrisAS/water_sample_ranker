"""Снимок текущего дерева, privacy gates и атомарная передача четырёх файлов."""

from __future__ import annotations

import argparse
import ast
import io
import json
import os
import re
import subprocess
import sys
import tarfile
import tempfile
import tomllib
from datetime import UTC, datetime
from pathlib import Path, PureWindowsPath
from typing import Any
from uuid import uuid4

from scripts import verify_handoff as policy
from water_sample_ranker.bundle import sync_directory
from water_sample_ranker.handoff import evidence


class Options:
    def __init__(
        self,
        *,
        run_checks: bool = True,
        sync_locked: bool = False,
        with_production: bool = False,
        frozen: bool = False,
        include_template: bool = False,
        timeout: float = 300,
    ) -> None:
        self.run_checks = run_checks
        self.sync_locked = sync_locked
        self.with_production = with_production
        self.frozen = frozen
        self.include_template = include_template
        self.timeout = timeout


def project_root(root: Path) -> Path:
    """An explicit project, never an ancestor inferred from the caller's cwd."""
    root = root.absolute()
    if any(p.is_symlink() for p in (root, *root.parents)):
        raise policy.HandoffError("PROJECT_SYMLINK")
    try:
        config = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
        if config["project"]["name"] != "water-sample-ranker":
            raise ValueError
        if not (root / "scripts/build_handoff.py").is_file():
            raise ValueError
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        top = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if top.returncode or Path(top.stdout.strip()).resolve() != root.resolve():
            raise ValueError
        # A public projection nested inside the real project is a source only.
        if any((p / ".git").exists() and (p / "pyproject.toml").is_file() for p in root.parents):
            raise ValueError
    except (OSError, ValueError, KeyError, subprocess.TimeoutExpired) as error:
        raise policy.HandoffError("PROJECT_ROOT_UNCONFIRMED") from error
    return root.resolve()


def canonical_output(root: Path, requested: Path) -> Path:
    if ".." in requested.parts or "\\" in str(requested) or PureWindowsPath(str(requested)).drive:
        # Native absolute Windows paths are checked by pathlib on Windows.
        if os.name != "nt" or ".." in requested.parts:
            raise policy.HandoffError("NONCANONICAL_OUTPUT_DIRECTORY")
    candidate = requested if requested.is_absolute() else root / requested
    expected = root / "artifacts/handoff"
    if any(p.is_symlink() for p in (candidate, *candidate.parents, expected, expected.parent)):
        raise policy.HandoffError("OUTPUT_SYMLINK")
    if candidate.resolve() != expected:
        raise policy.HandoffError("NONCANONICAL_OUTPUT_DIRECTORY")
    return expected


def identity(path: Path) -> tuple[int, int]:
    stat = path.lstat()
    return stat.st_dev, stat.st_ino


def bind_projection(root: Path, source: Path) -> None:
    """Only a clean, byte-verified public projection may supply release identity."""
    from scripts.public_gate import PRIVATE_DOCUMENTS, allowed_names, public_document, scan_tree

    if any(p.is_symlink() for p in (source, *source.parents)):
        raise policy.HandoffError("SOURCE_SYMLINK")
    top = subprocess.check_output(["git", "rev-parse", "--show-toplevel"], cwd=source, text=True)
    if Path(top.strip()).resolve() != source:
        raise policy.HandoffError("PROJECT_ROOT_UNCONFIRMED")
    names = allowed_names(root)
    scan_tree(source, history=False)
    for name in names:
        original = root / name
        if original.is_symlink():
            raise policy.HandoffError("SOURCE_SYMLINK")
        data = original.read_bytes()
        if name.startswith("docs/") and original.name in PRIVATE_DOCUMENTS:
            data = public_document(original.name)
        elif original.suffix == ".py":
            data, _ = projection(data)
        if (source / name).read_bytes() != data:
            raise policy.HandoffError("PUBLIC_PROJECTION_MISMATCH")
    if subprocess.check_output(["git", "status", "--porcelain"], cwd=source):
        raise policy.HandoffError("PUBLIC_PROJECTION_DIRTY")


def projection(data: bytes) -> tuple[bytes, list[str]]:
    text = data.decode("utf-8")
    lines = []
    changes = False
    for line in text.splitlines(keepends=True):
        if re.match(
            r'^[+ -]?"""(?:LoaderWindow interaction adapted|Adapted from gdis_loader by)', line
        ) or re.match(r"^[+ -]?Автор в исходном pyproject.toml", line):
            updated = policy.PERSON.sub("AUTHOR_REDACTED", line)
            changes = changes or updated != line
            line = updated
        lines.append(line)
    return "".join(lines).encode(), ["HEADER_PERSONAL_ATTRIBUTION_REDACTED"] if changes else []


def select_files(root: Path, include_template: bool) -> list[str]:
    names = {n for n in policy.ROOT_FILES if (root / n).is_file()}
    for directory in ("src/water_sample_ranker", "tests", "scripts", "docs", ".github/workflows"):
        base = root / directory
        if not base.exists():
            continue
        for p in base.rglob("*"):
            name = p.relative_to(root).as_posix()
            if p.is_file() and policy.allowed_source(name):
                names.add(name)
    if include_template:
        names.add("templates/Water_Sample_Ranking_Calculator.xlsx")
    seen = set()
    for name in names:
        key = policy.safe_path(name)
        if key in seen:
            raise policy.HandoffError("SOURCE_CASE_COLLISION")
        seen.add(key)
        path = root / name
        if path.is_symlink() or any(
            parent.is_symlink() for parent in path.parents if parent != root
        ):
            raise policy.HandoffError("SOURCE_SYMLINK")
    return sorted(names)


def git_state(root: Path, selected: list[str]) -> tuple[dict[str, Any], dict[str, bytes]]:
    from scripts.public_gate import PRIVATE_DOCUMENTS

    def git(*args: str) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            ["git", "--no-optional-locks", *args],
            cwd=root,
            capture_output=True,
            timeout=30,
            check=False,
            env={
                **{k: v for k, v in os.environ.items() if not k.startswith("GIT_")},
                "GIT_TERMINAL_PROMPT": "0",
            },
        )

    try:
        head = git("rev-parse", "--verify", "HEAD")
        branch = git("branch", "--show-current")
        status = git("status", "--porcelain=v1", "-z", "--untracked-files=all")
        if status.returncode:
            return {
                "status": "PARTIAL",
                "reason": "SOURCE_IDENTITY_PARTIAL",
                "head": None,
                "branch": None,
                "dirty": None,
            }, {}
        records = []
        all_states = []
        tokens = status.stdout.decode("utf-8").split("\0")
        index = 0
        excluded = 0
        while index < len(tokens):
            item = tokens[index]
            index += 1
            if not item:
                continue
            state, name = item[:2], item[3:]
            all_states.append(state)
            old = None
            if "R" in state or "C" in state:
                old = tokens[index]
                index += 1
            if name in selected and (old is None or old in selected):
                records.append({"state": state, "path": name, **({"previous": old} if old else {})})
            else:
                excluded += 1
        patches = {}
        diff_names = [
            n for n in selected if not (n.startswith("docs/") and Path(n).name in PRIVATE_DOCUMENTS)
        ]
        for label, args in (("WORKTREE", []), ("STAGED", ["--cached"])):
            diff = git("diff", "--no-ext-diff", "--no-textconv", *args, "--", *diff_names)
            if diff.returncode:
                raise policy.HandoffError("GIT_DIFF_FAILED")
            patches[f"evidence/{label}.patch"] = diff.stdout
        return {
            "status": "PASS" if head.returncode == 0 else "PARTIAL",
            "reason": "HEAD_AVAILABLE" if head.returncode == 0 else "SOURCE_IDENTITY_PARTIAL",
            "head": head.stdout.decode().strip() if head.returncode == 0 else None,
            "branch": branch.stdout.decode().strip() if branch.returncode == 0 else None,
            "dirty": bool(tokens[0]),
            "porcelain": records,
            "porcelain_scope": "ALLOWLIST_FILTERED",
            "full_porcelain_sha256": policy.sha(status.stdout),
            "total_state_counts": {
                "staged": sum(state[0] not in {" ", "?"} for state in all_states),
                "unstaged": sum(state[1] not in {" ", "?"} for state in all_states),
                "untracked": sum(state == "??" for state in all_states),
            },
            "excluded_status_entries": excluded,
            "staged": sum(r["state"][0] not in {" ", "?"} for r in records),
            "unstaged": sum(r["state"][1] not in {" ", "?"} for r in records),
            "untracked": sum(r["state"] == "??" for r in records),
        }, patches
    except OSError, subprocess.TimeoutExpired:
        return {
            "status": "PARTIAL",
            "reason": "SOURCE_IDENTITY_PARTIAL",
            "head": None,
            "branch": None,
            "dirty": None,
        }, {}


def snapshot(
    root: Path, options: Options
) -> tuple[dict[str, bytes], dict[str, Any], dict[str, Any]]:
    from scripts.public_gate import PRIVATE_DOCUMENTS, public_document

    names = select_files(root, options.include_template)
    git, patches = git_state(root, names)
    payloads = {}
    entries = {}
    for name in names:
        path = root / name
        if path.stat().st_size > policy.MAX_FILE:
            raise policy.HandoffError("SOURCE_SIZE_LIMIT")
        before = evidence.stamp(path)
        if before["bytes"] > policy.MAX_FILE:
            raise policy.HandoffError("SOURCE_SIZE_LIMIT")
        raw = path.read_bytes()
        if policy.sha(raw) != before["sha256"] or evidence.stamp(path) != before:
            raise policy.HandoffError("STALE_SOURCE_SNAPSHOT")
        if name.endswith(".xlsx"):
            policy.scan_xlsx(raw)
            data, transforms = raw, []
        else:
            data, transforms = projection(raw)
            if name.startswith("docs/") and path.name in PRIVATE_DOCUMENTS:
                data = public_document(path.name)
                transforms = ["PRIVATE_DOCUMENT_REPLACED"] if data != raw else []
            policy.scan_member(name, data)
        payloads["audit/" + name] = data
        entries[name] = {
            "original_sha256": before["sha256"],
            "original_bytes": len(raw),
            "archived_sha256": policy.sha(data),
            "archived_bytes": len(data),
            "mtime_ns": before["mtime_ns"],
            "type": "xlsx" if name.endswith(".xlsx") else "text",
            "transformations": transforms,
            "origin": "working-tree",
        }
    for name, raw in patches.items():
        data, transforms = projection(raw)
        policy.scan_text(name, data)
        payloads["audit/" + name] = data
        entries[name] = {
            "original_sha256": policy.sha(raw),
            "original_bytes": len(raw),
            "archived_sha256": policy.sha(data),
            "archived_bytes": len(data),
            "type": "text",
            "transformations": transforms,
            "origin": "git-diff",
        }
    source = {
        "fingerprint": policy.sha(policy.canonical(entries)),
        "git": git,
        "representation": "SHARE_SAFE_WORKTREE_PROJECTION",
        "file_count": len(entries),
    }
    return payloads, entries, source


def source_still_current(
    root: Path, options: Options, entries: dict[str, Any], source: dict[str, Any]
) -> None:
    _, latest, identity = snapshot(root, options)
    if latest != entries or identity != source:
        raise policy.HandoffError("STALE_SOURCE_SNAPSHOT")


def pack(path: Path, kind: str, hid: str, members: dict[str, bytes]) -> int:
    if len(members) > policy.MAX_FILES - 1 or sum(map(len, members.values())) > policy.MAX_TOTAL:
        raise policy.HandoffError("BUNDLE_SIZE_LIMIT")
    inventory = {
        "schema_version": policy.SCHEMA,
        "handoff_id": hid,
        "kind": kind,
        "excluded_self": f"{kind}/MANIFEST.json",
        "members": {
            n: {
                "sha256": policy.sha(v),
                "bytes": len(v),
                "type": "xlsx" if n.endswith(".xlsx") else "text",
            }
            for n, v in sorted(members.items())
        },
    }
    complete = members | {f"{kind}/MANIFEST.json": policy.canonical(inventory)}
    mode = "w:gz" if kind == "audit" else "w:xz"
    with tarfile.open(path, mode, format=tarfile.USTAR_FORMAT) as archive:
        for name, data in sorted(complete.items()):
            policy.safe_path(name)
            policy.scan_member(name, data)
            if len(data) > policy.MAX_FILE:
                raise policy.HandoffError("MEMBER_SIZE_LIMIT")
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o644
            info.mtime = 0
            info.uid = info.gid = 0
            archive.addfile(info, io.BytesIO(data))
    if path.stat().st_size > policy.MAX_ARCHIVE:
        raise policy.HandoffError("COMPRESSED_SIZE_LIMIT")
    return len(complete)


def code_map(payloads: dict[str, bytes]) -> str:
    lines = [
        "# Карта кода",
        "",
        "Ссылки ведут в соседний audit archive после распаковки обоих архивов.",
        "",
    ]
    for name, data in sorted(payloads.items()):
        if name.startswith("audit/src/") and name.endswith(".py"):
            tree = ast.parse(data)
            definitions = [
                n.name for n in tree.body if isinstance(n, (ast.ClassDef, ast.FunctionDef))
            ]
            lines.append(
                f"- [{name}]({'../' + name}): {', '.join(definitions) or 'пакет/константы'}"
            )
    config = payloads.get("audit/src/water_sample_ranker/config.py")
    if config:
        text = config.decode()
        for node in ast.parse(text).body:
            if isinstance(node, ast.ClassDef) and node.name == "CriteriaConfig":
                lines.extend(
                    [
                        "",
                        "## Реальные defaults / validation config",
                        "",
                        "```python",
                        ast.get_source_segment(text, node) or "",
                        "```",
                    ]
                )
    rules = payloads.get("audit/src/water_sample_ranker/criteria/rules.py")
    if rules:
        text = rules.decode()
        tree = ast.parse(text)
        lines.extend(["", "## Буквальная реализация для первичного ревью", "", "```python"])
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name in {
                "chlorine",
                "technical",
                "water_rate",
                "separator",
            }:
                lines.append(ast.get_source_segment(text, node) or "")
        lines.append("```")
    return "\n".join(lines) + "\n"


def review_report(
    hid: str,
    created: str,
    source: dict[str, Any],
    gates: dict[str, Any],
    versions: dict[str, Any],
    production: dict[str, Any],
    history: dict[str, Any],
    test_count: int | None,
) -> str:
    git = source["git"]
    verdict = (
        "FAIL" if any(g["status"] in {"FAIL", "BLOCKED"} for g in gates.values()) else "PARTIAL"
    )
    shown_count = test_count if test_count is not None else "UNKNOWN"
    rows = [
        f"# Независимый обзор Water Sample Ranker: {hid}",
        "",
        f"UTC: {created}",
        "",
        f"Verdict: {verdict}. Архивная целостность проверяется отдельно. Методика "
        "рейтинга не утверждена.",
        f"Python: {versions['python']}; Git HEAD: {git.get('head') or 'отсутствует'}; "
        f"branch: {git.get('branch') or 'UNKNOWN'}; dirty: {git.get('dirty')}.",
        f"Source fingerprint: `{source['fingerprint']}`. "
        "Это текущее дерево, включая безопасные staged/unstaged/untracked файлы.",
        "Копия двух заголовков с персональной атрибуцией обезличена; исходные SHA и "
        "transformations перечислены отдельно. Полная исходная постановка с "
        "персональными данными исключена.",
        "",
        "## Смотреть сначала",
        "",
        "1. reports/gates.json и логи: новые команды/exit code/timeout.",
        "2. reports/DOMAIN_AUDIT.md и coverage.json: synthetic отдельно от production.",
        "3. reports/excel_structure.json: структура, ёмкость и отсутствие финального score.",
        "4. CODE_MAP.md и audit/src: независимое чтение реализации и тестов.",
        "5. audit/docs/UX_PARITY_GDIS.md: историческая матрица, не новое ручное принятие.",
        "",
        "## Свежие gates",
        "",
        "| Gate | Статус |",
        "|---|---|",
    ]
    rows.extend(f"| {name} | {g['status']} |" for name, g in sorted(gates.items()))
    rows.extend(
        [
            "",
            f"Фактически собран pytest count: {shown_count}.",
            "РАСХОЖДЕНИЕ с историческим числом в documentation_claim ниже следует "
            "проверять явно: добавлены предметные и инфраструктурные регрессии A.1/B.",
            "Исторические claims source docs не считаются новыми доказательствами. "
            "Новые сценарии включают отрицательные измерения и drift AB27.",
            f"Исторический qualification: {json.dumps(history, ensure_ascii=False)}.",
            f"Production fresh execution: {production['status']}; "
            "прошлый PASS не заменяет новый запуск.",
            "",
            "## Область доказательств и ограничения",
            "",
            "Этап B сохраняет фонд/lifecycle/plast, пары ЛХА, №1–6 и сепаратор; "
            "добавляет typed ГСС, timeline и кустовые candidates. №7–9 UNKNOWN; UNKNOWN не "
            "равно нулевому баллу.",
            "Stage A.1 исправляет валидацию измерений и схемы, сохраняет методику. Критерий "
            "№1, итоговое агрегирование и правила UNKNOWN требуют согласования заказчика.",
            "Synthetic boundary tests и headless widgets не доказывают native "
            "Windows/Excel UX. Windows/Excel/visual "
            "review: PENDING, gates NOT_RUN.",
            "Template binary по умолчанию исключён. При его отсутствии полная повторная "
            "проверка исходников вне проекта потребует безопасного шаблона; JSON "
            "inventory не заменяет XLSX.",
            "Baseline/after и ограничения: audit/docs/STAGE_B_REPORT.md и STAGE_A1_REPORT.md. "
            "Приватный discrepancy register исключён; unresolved cases требуют сверки источников.",
            "Policy content scan не является математическим доказательством отсутствия "
            "всех возможных форм персональных данных. Нет production rows, секретов или "
            "operator paths по принятой политике.",
            "Квитанция подтверждает целостность и политику упаковки, а не "
            "BUSINESS_CORRECTNESS_PASS; хеши не являются цифровой подписью против "
            "согласованной подмены всего комплекта.",
            "",
            "## Состав",
            "",
            "Audit tar.gz + review tar.xz + handoff JSON + verification JSON. Передайте "
            "ChatGPT все четыре файла.",
            "Для stdlib проверки извлеките review/verify_handoff.py и запустите его "
            "рядом с четырьмя файлами. Он не требует исходного дерева, данных или сети.",
        ]
    )
    return "\n".join(rows) + "\n"


def build(root: Path, output: Path, options: Options, *, source_root: Path | None = None) -> Path:
    root = project_root(root)
    output = canonical_output(root, output)
    source_root = source_root.resolve() if source_root is not None else root
    if source_root != root:
        bind_projection(root, source_root)
    root_identity = identity(root)
    output.mkdir(parents=True, exist_ok=True)
    output_identity = identity(output)

    def current() -> None:
        if identity(root) != root_identity or canonical_output(root, output) != output:
            raise policy.HandoffError("OUTPUT_DIRECTORY_CHANGED")
        if identity(output) != output_identity:
            raise policy.HandoffError("OUTPUT_DIRECTORY_CHANGED")
        if source_root != root:
            bind_projection(root, source_root)

    created = datetime.now(UTC).isoformat()
    hid = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:12]
    active, inputs = evidence.input_inventory(root)
    protected_before = evidence.protected(root, active)
    payloads, entries, source = snapshot(source_root, options)
    gates, logs, versions = evidence.checks(
        source_root, options.run_checks, options.sync_locked, options.frozen, options.timeout
    )
    excel = evidence.excel_inventory(active["calculator"])
    excel["binary"] = (
        "SCANNED_BLANK_TEMPLATE_INCLUDED"
        if options.include_template
        else "TEMPLATE_BINARY_EXCLUDED"
    )
    gates["excel_structural"] = {"status": excel["status"]}
    gates["template_binary"] = {
        "status": "PASS" if options.include_template else "PARTIAL",
        "reason": "SCANNED_BLANK_TEMPLATE"
        if options.include_template
        else "TEMPLATE_BINARY_EXCLUDED",
    }
    history = evidence.historical(root)
    acceptance = root / "docs/ACCEPTANCE.md"
    if acceptance.is_file():
        claim = re.search(r"pytest\s*—\s*(\d+) passed", acceptance.read_text(encoding="utf-8"))
        history["documentation_claim"] = {
            "source": "docs/ACCEPTANCE.md",
            "sha256": evidence.stamp(acceptance)["sha256"],
            "pytest_count": int(claim.group(1)) if claim else None,
        }
    spec = root / "docs/SPEC.md"
    if spec.is_file():
        inputs["specification"] = evidence.stamp(spec)
    prod = {
        "status": "NOT_RUN",
        "fresh_execution": False,
        "reason": "EXPLICIT_PRODUCTION_OPTION_REQUIRED",
    }
    if options.with_production:
        with tempfile.TemporaryDirectory(prefix="wsr-prod-evidence-") as temp:
            aggregate_file = Path(temp) / "aggregate.json"
            cmd = [
                sys.executable,
                "-B",
                "-m",
                "water_sample_ranker.handoff.worker",
                str(root),
                str(aggregate_file),
            ]
            gate, data = evidence.run_gate(cmd, root, options.timeout, "production")
            # В записанной команде путь временного aggregate файла логический.
            gate["command"][-1] = "<TEMP>/aggregate.json"
            gates["production_execution"], logs[gate["log"]] = gate, data
            if aggregate_file.is_file():
                prod = policy.parse_json(aggregate_file.read_bytes())
            else:
                prod = {
                    "status": gate["status"],
                    "fresh_execution": True,
                    "reason": "NO_COMPLETE_AGGREGATE",
                }
    if prod.get("source_fingerprints"):
        inputs["fresh_production_roles"] = prod["source_fingerprints"]
    gates["production_validation"] = {
        "status": prod["status"],
        "fresh_execution": prod.get("fresh_execution", False),
    }
    gates["source_identity"] = {
        "status": source["git"]["status"],
        "reason": source["git"].get("reason"),
    }
    gates["rating_methodology"] = {"status": "UNKNOWN", "reason": "CUSTOMER_DECISIONS_PENDING"}
    release_receipt = root / "artifacts/private/public-release.json"
    release_data = None
    if release_receipt.is_file():
        release_data = policy.limited_read(release_receipt)
        policy.check_public_release(release_data, source["git"]["head"])
        gates["public_release"] = {"status": "PASS", "scope": "STATIC_DOWNLOADED_EVIDENCE_ONLY"}
    source_still_current(source_root, options, entries, source)
    if evidence.protected(root, active) != protected_before:
        raise policy.HandoffError("INPUTS_CHANGED")
    gates["input_preservation"] = {"status": "PASS"}
    tests = logs.get("review/logs/pytest.txt", b"").decode()
    match = re.search(r"(\d+) passed", tests)
    test_count = int(match.group(1)) if match else None
    domain = (
        "# Предметный аудит\n\n"
        "Свежие synthetic доказательства: tests/test_water_rules.py, "
        "test_measurement_semantics.py, test_lha_quality.py, test_lifecycle_water.py, "
        "test_formula_golden.py; "
        "статусы и фактический pytest stdout — gates.json/logs.\n\n"
        "№1: дни/месяцы, без утверждённой score-функции. №2/3: строгие границы "
        "Cl 2000/8000, минерализация 22000 и интервал >7 суток. №4/5: генезис "
        "AB/cache/error и scoped исключение №23. №6: Qв>1000 л/сут и Cl>2000. "
        "ПК1: сепаратор при Qв>=6.912 м³/сут. Методика не менялась.\n\n"
        "A.1: отрицательные Cl/M/Qв имеют INVALID_MEASUREMENT с исходным evidence; "
        "зависимые решения UNKNOWN, ноль допустим. AB27 требует literal Заключение; "
        "drift блокирует источник. FRESHNESS_POLICY_PENDING не задаёт срок годности.\n\n"
        "№7–9: Truth UNKNOWN; typed ГСС/timeline/кустовые candidates реализованы. "
        "Интерпретация — METHODOLOGY_PENDING; недостающий файл — SOURCE_UNAVAILABLE. "
        "NOT_IMPLEMENTED относится только к отсутствующей функции, NOT_RUN — к "
        "непроведённой проверке. Числовой рейтинг не утверждён.\n\n"
        f"Свежая production проверка: {prod['status']}. "
        "Данные в coverage.json — агрегаты явного opt-in запуска; без флага численность "
        "фонда и matching не объявляются проверенными.\n\n"
        "Фонд: C/D/G/H, без J. При opt-in численность/категории/сверки/двойной "
        "счёт/этапы и TRUE/FALSE/UNKNOWN/N/A вычисляются из свежего service "
        "result, без зашитых контрольных чисел. Исходные аномалии остаются в "
        "diagnostic_counts.\n"
    )
    context = {"schema_version": policy.SCHEMA, "handoff_id": hid, "source": source}
    payloads["audit/CONTEXT.json"] = policy.canonical(context)
    payloads["audit/SOURCE_MANIFEST.json"] = policy.canonical(
        {
            "files": entries,
            "excluded_groups": [
                "production",
                "reference",
                "raw_specification",
                "private_artifacts",
                "unsafe_or_unlisted_docs",
                "synthetic_workbook_binary",
            ],
        }
    )
    privacy = {
        "status": "PASS",
        "mode": "share-safe",
        "raw_rows_included": False,
        "scope": "ALLOWLIST_AND_CONTENT_POLICY_SCAN",
        "excluded": [
            "data",
            "reference workbooks",
            "private reports/logs",
            "operator paths",
            "credentials",
            "raw specification",
        ],
        "template_binary_included": options.include_template,
        "semantic_privacy_proof": False,
    }
    review = {
        "review/CONTEXT.json": policy.canonical(context),
        "review/REPORT.md": review_report(
            hid, created, source, gates, versions, prod, history, test_count
        ).encode(),
        "review/CODE_MAP.md": code_map(payloads).encode(),
        "review/verify_handoff.py": (root / "scripts/verify_handoff.py").read_bytes(),
        "review/reports/DOMAIN_AUDIT.md": domain.encode(),
        "review/reports/gates.json": policy.canonical(gates),
        "review/reports/coverage.json": policy.canonical(prod),
        "review/reports/source_inventory.json": policy.canonical(inputs),
        "review/reports/excel_structure.json": policy.canonical(excel),
        "review/reports/privacy.json": policy.canonical(privacy),
        "review/reports/preservation.json": policy.canonical(
            {
                "status": "PASS",
                "protected_files": len(protected_before),
                "changed": 0,
                "before_fingerprint": policy.sha(
                    policy.canonical(sorted(protected_before.values(), key=lambda x: x["sha256"]))
                ),
                "after_fingerprint": policy.sha(
                    policy.canonical(sorted(protected_before.values(), key=lambda x: x["sha256"]))
                ),
                "scope": "SHA256_SIZE_MTIME_AND_MEMBERSHIP",
            }
        ),
        **logs,
    }
    if release_data is not None:
        review["review/reports/public_release.json"] = release_data
    for name, data in (payloads | review).items():
        policy.scan_member(name, data)
    current()
    published: list[tuple[Path, tuple[int, int]]] = []
    with tempfile.TemporaryDirectory(prefix=".handoff-stage-", dir=output.parent) as staging:
        stage = Path(staging)
        artifacts: dict[str, Any] = {}
        for kind, suffix, files in (("audit", ".tar.gz", payloads), ("review", ".tar.xz", review)):
            filename = f"water-sample-ranker-{kind}-{hid}{suffix}"
            count = pack(stage / filename, kind, hid, files)
            raw = (stage / filename).read_bytes()
            artifacts[kind] = {
                "path": filename,
                "bytes": len(raw),
                "sha256": policy.sha(raw),
                "members": count,
            }
        status = (
            "FAIL" if any(g["status"] in {"FAIL", "BLOCKED"} for g in gates.values()) else "PARTIAL"
        )
        manifest = {
            "schema_version": policy.SCHEMA,
            "handoff_id": hid,
            "created_utc": created,
            "mode": "share-safe",
            "status": status,
            "source": source,
            "inputs": inputs,
            "gates": gates,
            "artifacts": artifacts,
            "required_evidence": {k: sorted(v) for k, v in policy.REQUIRED.items()},
            "verification": f"water-sample-ranker-handoff-verification-{hid}.json",
        }
        manifest_name = f"water-sample-ranker-handoff-{hid}.json"
        (stage / manifest_name).write_bytes(policy.sealed(manifest))
        verifier = root / "scripts/verify_handoff.py"

        def independent(path: Path, create: bool) -> None:
            with tempfile.TemporaryDirectory(prefix="wsr-independent-") as cwd:
                process = subprocess.run(
                    [
                        sys.executable,
                        "-I",
                        "-B",
                        str(verifier),
                        str(path),
                        *(["--create-receipt"] if create else []),
                    ],
                    cwd=cwd,
                    capture_output=True,
                    timeout=options.timeout,
                    check=False,
                )
            if process.returncode:
                raise policy.HandoffError("INDEPENDENT_VERIFICATION_FAILED")

        independent(stage / manifest_name, True)
        if release_data is not None and release_receipt.read_bytes() != release_data:
            raise policy.HandoffError("STALE_PUBLIC_RELEASE_RECEIPT")
        source_still_current(source_root, options, entries, source)
        if evidence.protected(root, active) != protected_before:
            raise policy.HandoffError("INPUTS_CHANGED")
        order = [d["path"] for d in artifacts.values()] + [manifest["verification"], manifest_name]
        for filename in order:
            with (stage / filename).open("rb") as stream:
                os.fsync(stream.fileno())
        # Detect case/Unicode aliases too, including leftovers from an earlier crash.
        expected_names = {policy.safe_path(name) for name in order}
        if any(policy.safe_path(p.name) in expected_names for p in output.iterdir()):
            raise policy.HandoffError("OUTPUT_COLLISION")
        try:
            for filename in order:
                current()
                target = output / filename
                own_identity = identity(stage / filename)
                os.link(stage / filename, target)  # atomic no-replace, staging на той же FS
                published.append((target, own_identity))
            sync_directory(output)
            independent(output / manifest_name, False)
            current()
            source_still_current(source_root, options, entries, source)
            if evidence.protected(root, active) != protected_before:
                raise policy.HandoffError("INPUTS_CHANGED")
        except BaseException:
            for path, own_identity in reversed(published):
                if path.exists() and identity(path) == own_identity:
                    path.unlink()
            raise
    return output / manifest_name


def main(argv: list[str] | None = None, *, root: Path | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--mode", choices=["share-safe"], default="share-safe")
    p.add_argument("--output-dir", type=Path, default=Path("artifacts/handoff"))
    p.add_argument(
        "--public-source-root",
        type=Path,
        help="Clean public Git projection; final output stays in the real project",
    )
    p.add_argument("--skip-checks", action="store_true")
    p.add_argument("--sync-locked", action="store_true")
    p.add_argument("--with-production-validation", action="store_true")
    p.add_argument("--with-frozen-smoke", action="store_true")
    p.add_argument("--include-template-binary", action="store_true")
    p.add_argument("--timeout", type=float, default=300)
    args = p.parse_args(argv)
    root = (root or Path(__file__).resolve().parents[3]).resolve()
    output = args.output_dir if args.output_dir.is_absolute() else root / args.output_dir
    options = Options(
        run_checks=not args.skip_checks,
        sync_locked=args.sync_locked,
        with_production=args.with_production_validation,
        frozen=args.with_frozen_smoke,
        include_template=args.include_template_binary,
        timeout=args.timeout,
    )
    try:
        if not 0 < args.timeout <= 3600:
            raise policy.HandoffError("TIMEOUT_RANGE")
        manifest = build(root, output, options, source_root=args.public_source_root)
    except (Exception, KeyboardInterrupt) as error:
        category = str(error) if isinstance(error, policy.HandoffError) else "INTERNAL_ERROR"
        if not re.fullmatch("[A-Z_]+", category):
            category = "INTERNAL_ERROR"
        print("BLOCKED: " + category)
        return 2
    receipt = policy.parse_json(manifest.read_bytes(), seal=True)
    for name in [
        *(v["path"] for v in receipt["artifacts"].values()),
        manifest.name,
        receipt["verification"],
    ]:
        file = manifest.parent / name
        digest = policy.sha(file.read_bytes())
        print(f"{file.absolute()}  {file.stat().st_size} bytes  SHA256={digest}")
    print(
        "HANDOFF "
        + receipt["status"]
        + "; INDEPENDENT INTEGRITY PASS; BUSINESS CORRECTNESS UNKNOWN"
    )
    return 0
