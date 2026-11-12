"""Shared read-only validation and exclusive-copy update for CLI and Qt."""

import json
import os
import platform
import tempfile
import xml.etree.ElementTree as ET
from collections import Counter
from copy import deepcopy
from dataclasses import asdict, dataclass, replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from zipfile import BadZipFile

from water_sample_ranker import __version__
from water_sample_ranker.bundle import (
    BundleError,
    OwnedFile,
    file_record,
    link_exclusive,
    publish_json_exclusive,
    verify_bundle,
)
from water_sample_ranker.config import OPTIONAL_ROLES, ROLES, AppConfig
from water_sample_ranker.criteria.rules import evaluate, invalid, separator, water_rate
from water_sample_ranker.criteria.rules import result as criterion_result
from water_sample_ranker.diagnostics import attach_evidence, evidence_aggregates, freshness
from water_sample_ranker.domain.models import RankedWell, Truth
from water_sample_ranker.domain.scoring import confirmed_score, coverage, unavailable_weight
from water_sample_ranker.excel.compatibility import changed_parts, inspect_template
from water_sample_ranker.excel.manual import identity_key, read_marks, read_operator_settings
from water_sample_ranker.excel.writer import write
from water_sample_ranker.matching.current_plast import resolve_current
from water_sample_ranker.matching.matcher import complementary_gate, current_samples, select_pair
from water_sample_ranker.matching.normalize import normalize_field_name, normalize_well_id
from water_sample_ranker.progress import ProgressCallback, ProgressUpdate
from water_sample_ranker.readers.fund import read_fund
from water_sample_ranker.readers.gdi import read_gdi
from water_sample_ranker.readers.lha import read_lha
from water_sample_ranker.readers.monitoring import MonitoringHistory, read_history
from water_sample_ranker.readers.optional import OptionalInputs
from water_sample_ranker.readers.well_history import read_well_history
from water_sample_ranker.source_manifest import fingerprint
from water_sample_ranker.validation.reconciliation import reconcile
from water_sample_ranker.validation.sources import inventory


class ServiceError(RuntimeError):
    pass


@dataclass(slots=True)
class ServiceResult:
    report: dict[str, Any]
    rows: tuple[RankedWell, ...] = ()
    output: Path | None = None
    owned_files: tuple[OwnedFile, ...] = ()

    @property
    def has_errors(self) -> bool:
        return bool(self.report.get("errors"))

    def summary(self) -> str:
        r = self.report
        errors = "\n".join(r.get("errors", []))
        lines = [
            f"Фонд: {r.get('fund_total', 0)}; подготовлено: {len(self.rows)}",
            "Месторождения: " + ", ".join(r.get("available_fields", [])),
            "Предварительный рейтинг / методика частично согласована.",
            f"Пороги: {r.get('criteria_policy_status', 'LITERAL_V0_2')}",
        ]
        reconciliation = r.get("fund_reconciliation", {})
        if reconciliation.get("known_mismatch_observed"):
            lines.append(
                "РАСХОЖДЕНИЕ ФОНДА: детализация C/D/G/H="
                f"{reconciliation.get('detail_scope_C_D_G_H')}; итог C/D/I="
                f"{reconciliation.get('grand_C_D_I')}; детализация K="
                f"{reconciliation.get('detail_K')}; итог K="
                f"{reconciliation.get('grand', {}).get('K')}. Источник не исправлялся."
            )
        if r.get("lha"):
            lines.append("Последняя датированная проба в источнике: " + str(r["lha"]["last_date"]))
        freshness = r.get("source_freshness", {})
        if freshness:
            lines.append(
                "ВНИМАНИЕ: актуальность ЛХА требует подтверждения; "
                f"дата источника {freshness['source_date']}, "
                f"фонд {freshness['selected_fund_date']}, "
                f"расчёт {freshness['calculation_date']}. "
                "Допустимая давность не согласована (FRESHNESS_POLICY_PENDING)."
            )
        lines.append(f"Диагностика: {len(r.get('issues', []))}; ошибки: {len(r.get('errors', []))}")
        lines.extend(
            f"{code}: {count}"
            for code, count in Counter(i["code"] for i in r.get("issues", [])).items()
        )
        if self.output:
            lines.append(f"Результат: {self.output}")
        if r.get("deprecated_inputs_ignored"):
            lines.append("Устаревшие пути плана ГТМ/геоданных проигнорированы; файлы не читались.")
        lines.append("№6: PASS ожидает предметное правило; ручной ГТМ задаётся в Excel.")
        for role, state in r.get("optional_sources", {}).items():
            lines.append(f"Необязательный {role}: {state['status']}; {state['message']}")
        if errors:
            lines.append(errors)
        return "\n".join(lines)


def run_validate(
    config: AppConfig, *, as_of: date | None = None, progress: ProgressCallback | None = None
) -> ServiceResult:
    day = as_of or date.today()

    def emit(percent: int, text: str) -> None:
        if progress:
            progress(ProgressUpdate(percent, text))

    emit(0, "Проверка путей")
    report: dict[str, Any] = {
        "timestamp": datetime.now(UTC).isoformat(),
        "version": __version__,
        "python": platform.python_version(),
        "calculation_date": day.isoformat(),
        "errors": [],
        "issues": [],
        "source_manifest": {},
        "source_changed_count": 0,
        "schemas": {},
        "criteria_snapshot": asdict(config.criteria),
        "criteria_policy_status": config.criteria.policy_status,
        "rating_methodology": "PROVISIONAL",
        "methodology_status": {
            "criterion_1": "CALENDAR_MONTHS_CLAMPED_PROVISIONAL",
            "criterion_6": "GATE_ONLY_SECOND_STEP_PENDING",
            "criterion_9": "DISABLED_BY_CUSTOMER",
            "tie_policy": "COMPETITION_TECHNICAL_DRAFT",
        },
        "available_fields": [],
        "config_source": str(config.root / "config.toml"),
        "visual": "VISUAL_REVIEW_PENDING",
        "windows": "WINDOWS_PENDING",
    }
    result = ServiceResult(report)
    paths = {r: config.path(r) for r in ("calculator", *ROLES, "manual_input")}
    report["deprecated_inputs_ignored"] = [r for r in OPTIONAL_ROLES if config.path(r)]
    try:
        for role in ("calculator", "fund", "lha", "well_history"):
            path = paths[role]
            if path is None or not path.is_file():
                report["errors"].append(f"SOURCE_REQUIRED: {role}")
        for role, candidates in config.extra.get("discovery_ambiguities", {}).items():
            if config.path(role) is None and role in {"fund", "lha", "well_history"}:
                report["errors"].append(f"SOURCE_ROLE_AMBIGUOUS: {role}: {candidates}")
        if report["errors"]:
            return result
        for role, p in paths.items():
            if p is not None:
                if (
                    role not in {"calculator", "fund", "lha", "well_history", "manual_input"}
                    and not p.is_file()
                ):
                    paths[role] = None
                    report.setdefault("optional_source_warnings", []).append(
                        role + ": SOURCE_UNAVAILABLE"
                    )
                    continue
                if role == "manual_input":
                    if p.is_file():
                        report["source_manifest"][role] = fingerprint(p)
                    continue
                if p.suffix.lower() not in {".xlsx", ".xlsm"}:
                    raise ServiceError(f"SOURCE_EXTENSION: {role}")
                report["source_manifest"][role] = fingerprint(p)
                report["schemas"][role] = inventory(p)
        calculator = paths["calculator"]
        assert calculator is not None
        report["template"] = inspect_template(calculator)
        emit(15, "Чтение фонда")
        fundpath = paths["fund"]
        assert fundpath is not None
        fund = read_fund(fundpath, day)
        report["issues"].extend(i.to_dict() for i in fund.issues)
        report["errors"].extend(
            i.code + ": " + i.message for i in fund.issues if i.severity == "error"
        )
        report["fund_total"] = len(fund.wells)
        report["fund_date"] = fund.fund_date.isoformat() if fund.fund_date else None
        report["fund_reconciliation"] = reconcile(fundpath, fund)
        report["fund_categories"] = dict(Counter(w.source_category.value for w in fund.wells))
        fields = sorted({w.normalized_field for w in fund.wells})
        report["available_fields"] = fields
        if not fields:
            raise ServiceError("FUND_EMPTY")
        emit(28, "История скважин и подтвержденные этапы")
        historypath = paths["well_history"]
        assert historypath is not None
        histories = []
        for field in fields:
            parsed = read_well_history(historypath, field)
            histories.extend(parsed.histories)
            report["issues"].extend(i.to_dict() for i in parsed.issues)
            if not parsed.histories:
                report["errors"].append(f"HISTORY_SCHEMA_OR_DATA_MISSING: {field}")
        history = tuple(histories)
        report["history_records"] = len(history)
        emit(42, "Чтение ЛХА: значения, формулы и cached results")
        lhapath = paths["lha"]
        assert lhapath is not None
        samples, issues = read_lha(lhapath)
        report["issues"].extend(i.to_dict() for i in issues)
        report["errors"].extend(i.code + ": " + i.message for i in issues if i.severity == "error")
        report["lha"] = {
            "records": len(samples),
            "sources": dict(Counter(s.source for s in samples)),
            "last_date": max(
                (s.date.isoformat() for s in samples if s.date and s.date.date() <= day),
                default=None,
            ),
            "invalid_dates": sum(s.date is None for s in samples),
            "future_records": sum(bool(s.date and s.date.date() > day) for s in samples),
            "cache_status": dict(Counter(e.status for s in samples for e in s.evidence)),
            "invalid_measurements": {
                "total": sum(len(s.invalid_measurements) for s in samples),
                "by_source": dict(
                    Counter(s.source for s in samples for _ in s.invalid_measurements)
                ),
                "by_field": dict(Counter(s.field for s in samples for _ in s.invalid_measurements)),
                "by_measurement": dict(Counter(k for s in samples for k in s.invalid_measurements)),
                "affected_observations": sum(bool(s.invalid_measurements) for s in samples),
            },
        }
        source_freshness: dict[str, Any] = {
            "source_date": report["lha"]["last_date"],
            "selected_fund_date": report["fund_date"],
            "calculation_date": day.isoformat(),
            "status": "FRESHNESS_POLICY_PENDING",
        }
        report["source_freshness"] = source_freshness
        gdipath = paths["gdi"]
        gdi = read_gdi(gdipath) if gdipath else ()
        report["gdi_identity_observations"] = len(gdi)
        emit(60, "История штуцеров и необязательные источники")
        monitorpath = paths["monitoring"]
        try:
            monitoring = (
                read_history(monitorpath, day)
                if monitorpath and monitorpath.is_file()
                else MonitoringHistory(reasons=("SOURCE_UNAVAILABLE",))
            )
        except OSError, ValueError, KeyError, BadZipFile, ET.ParseError:
            monitoring = MonitoringHistory(reasons=("OPTIONAL_SOURCE_INVALID",))
            report.setdefault("optional_source_warnings", []).append(
                "monitoring: OPTIONAL_SOURCE_INVALID"
            )
        monitoring_summary: dict[str, Any] = {
            "choke_rows": monitoring.choke_rows,
            "observations": len(monitoring.observations),
            "future_cells": monitoring.future_cells,
            "reasons": monitoring.reasons,
            "suspicion_groups": [asdict_location_group(g) for g in monitoring.groups],
        }
        report["monitoring"] = monitoring_summary
        plan = OptionalInputs(status="DISABLED", message="План ГТМ больше не используется")
        geo = OptionalInputs(status="DISABLED", message="Отложен заказчиком")
        report["optional_sources"] = {
            role: {"status": source.status, "message": source.message}
            for role, source in (("gtm_plan", plan), ("geodata", geo))
        }
        emit(72, "Идентичности, актуальные пробы и предварительные вклады")
        operator_path = paths["manual_input"] or calculator
        marks = read_marks(operator_path)
        operator_settings = read_operator_settings(operator_path)
        operator_files = (
            sorted(operator_path.glob("*.xlsx")) if operator_path.is_dir() else [operator_path]
        )
        report["operator_manifest"] = {str(p): fingerprint(p) for p in operator_files}
        report["operator_settings"] = operator_settings
        report["operator_marks_loaded"] = len(marks)
        settings_by_field = (
            {field: read_operator_settings(operator_path, field) for field in fields}
            if operator_path.is_dir()
            else {}
        )
        rows = []
        for well in fund.wells:
            operator_settings = (
                settings_by_field[well.normalized_field]
                if operator_path.is_dir()
                else operator_settings
            )
            identity = resolve_current(well, history, gdi, day)
            pair = select_pair(identity, samples, history, day)
            slide = select_pair(identity, samples, history, day, "slide")
            criteria, days, months = evaluate(identity, pair, slide, day, config.criteria)
            current = current_samples(identity, samples, history, day)
            gate_pair = complementary_gate(pair, current)
            gate = water_rate(gate_pair, config.criteria)
            gate = replace(gate, diagnostic_reason=gate.diagnostic_reason + "; " + gate_pair.reason)
            criteria = (*criteria[:5], gate, *criteria[6:])
            temp_row = RankedWell(identity, pair, slide, criteria, days, months, "", None, 0)
            mark = marks.get(identity_key(temp_row))
            if mark:
                c8 = criterion_result(
                    8,
                    Truth.TRUE
                    if mark[5] == "Да"
                    else Truth.FALSE
                    if mark[5] == "Нет"
                    else Truth.UNKNOWN,
                    pair,
                    "MANUAL_GTM_" + str(mark[5]),
                )
                criteria = (*criteria[:7], c8, criteria[8])
                when, before, after, basis = mark[6:10]
                if (
                    isinstance(when, (date, datetime))
                    and pair.last
                    and pair.last.date
                    and isinstance(before, (float, int))
                    and isinstance(after, (float, int))
                    and basis
                ):
                    event_day = when.date() if isinstance(when, datetime) else when
                    if pair.last.date.date() < event_day <= day and after > before > 0:
                        c7 = criterion_result(
                            7, Truth.TRUE, pair, "MANUAL_CONFIRMED_DIAMETER_INCREASE_AFTER_SAMPLE"
                        )
                        criteria = (*criteria[:6], c7, *criteria[7:])
            factual_criteria = criteria
            adjusted = []
            for c in criteria:
                weight = float(operator_settings.get(f"D{c.number + 20}", c.weight))
                enabled = operator_settings.get(f"I{c.number + 20}", "Да") == "Да"
                if c.number < 9 and not enabled:
                    c = replace(
                        c, state=Truth.DISABLED, normalized_score=None, weighted_contribution=None
                    )
                if (
                    c.number == 1
                    and operator_settings.get("B13", "Полные календарные месяцы")
                    != "Полные календарные месяцы"
                ):
                    c = replace(
                        c,
                        state=Truth.PENDING_CUSTOMER_RULE,
                        normalized_score=None,
                        weighted_contribution=None,
                    )
                adjusted.append(
                    replace(
                        c,
                        weight=weight,
                        weighted_contribution=c.normalized_score * weight
                        if c.normalized_score is not None
                        else None,
                    )
                )
            criteria = tuple(adjusted)
            cover, unknown = coverage(criteria)
            rows.append(
                RankedWell(
                    identity,
                    pair,
                    slide,
                    criteria,
                    days,
                    months,
                    separator(latest_water_pair(current, pair), identity.plast, config.criteria),
                    cover,
                    unknown,
                    factual_criteria=factual_criteria,
                    gate_pair=gate_pair,
                    separator_pair=latest_water_pair(current, pair),
                )
            )
        result.rows = attach_evidence(
            tuple(rows), history, monitoring, plan, geo, day, archive_neighbors=False
        )
        rows = list(result.rows)
        report["evidence_stage_b"] = evidence_aggregates(result.rows)
        # Private register: raw source identities and coordinates never enter handoff.
        series_register: dict[tuple[str, str | None, str], dict[str, Any]] = {}
        for observation in monitoring.observations:
            loc = observation.measurement.location
            key = (loc.file, loc.sheet, "".join(c for c in (loc.cell or "") if c.isdigit()))
            if key in series_register:
                continue
            candidates = [
                r
                for r in rows
                if r.identity.fund.normalized_field == observation.field
                and r.identity.fund.base_well_id
                == normalize_well_id(observation.display_well).base_well_id
            ]
            exact = [
                r.identity.display
                for r in candidates
                if any(
                    o.display_well == observation.display_well
                    and o.identity_locations == observation.identity_locations
                    for e in r.diagnostic_evidence
                    if e.number == 7
                    for o in e.observations
                )
            ]
            series_register[key] = {
                "field": observation.field,
                "well": observation.display_well,
                "gsk": observation.gsk,
                "pad": observation.pad,
                "object": observation.raw_object,
                "source_locations": [asdict(origin) for origin in observation.identity_locations],
                "review_targets": exact,
                "status": "EVIDENCE_RETAINED_FOR_REVIEW"
                if exact
                else "IDENTITY_OR_STAGE_UNRESOLVED"
                if candidates
                else "OUTSIDE_SELECTED_FUND",
            }
        report["monitoring"]["series_register"] = list(series_register.values())
        report["monitoring"]["series_binding_counts"] = dict(
            Counter(s["status"] for s in series_register.values())
        )
        report["source_freshness"].update(freshness(result.rows, day))
        for key, value in (
            ("fund_age_days", report["fund_date"]),
            ("lha_source_age_days", report["lha"]["last_date"]),
        ):
            report["source_freshness"][key] = (
                (day - date.fromisoformat(value[:10])).days if value else None
            )
        counts = Counter(
            (
                r.identity.fund.normalized_field,
                r.identity.display,
                r.identity.stage.stage_sequence if r.identity.stage else None,
                r.identity.plast,
            )
            for r in rows
        )
        collisions = [key for key, n in counts.items() if n > 1]
        if collisions:
            report["errors"].append(f"TARGET_IDENTITY_COLLISION: {len(collisions)}")
        report["coverage"] = {
            field: {
                "rows": sum(r.identity.fund.normalized_field == field for r in rows),
                "identity": dict(
                    Counter(
                        r.identity.status for r in rows if r.identity.fund.normalized_field == field
                    )
                ),
                "lha_last": sum(
                    r.pair.last is not None
                    for r in rows
                    if r.identity.fund.normalized_field == field
                ),
                "pair_policies": dict(
                    Counter(
                        r.pair.reason for r in rows if r.identity.fund.normalized_field == field
                    )
                ),
                "criteria": {
                    str(i): dict(
                        Counter(
                            r.criteria[i - 1].state.value
                            for r in rows
                            if r.identity.fund.normalized_field == field
                        )
                    )
                    for i in range(1, 10)
                },
            }
            for field in fields
        }
        report["preliminary_scores"] = [
            {
                "field": r.identity.fund.normalized_field,
                "well": r.identity.display,
                "score": confirmed_score(r.criteria),
                "unavailable_weight": unavailable_weight(r.criteria),
                "rank": 1
                + sum(
                    confirmed_score(other.criteria) > confirmed_score(r.criteria)
                    for other in rows
                    if other.identity.fund.normalized_field == r.identity.fund.normalized_field
                ),
            }
            for r in rows
        ]
        report["rows"] = [asdict(r) for r in rows]
        emit(94, "Проверка неизменности источников")
        for p, before in report["operator_manifest"].items():
            if fingerprint(Path(p)) != before:
                report["errors"].append("MANUAL_SOURCE_CHANGED")
        for role, p in paths.items():
            if (
                p is not None
                and role in report["source_manifest"]
                and fingerprint(p) != report["source_manifest"][role]
            ):
                report["source_changed_count"] += 1
                report["errors"].append(f"SOURCE_CHANGED_DURING_READ: {role}")
        if not result.has_errors:
            emit(100, "Проверка завершена")
        return result
    except (OSError, ValueError, KeyError, BadZipFile, ET.ParseError, ServiceError) as error:
        report["errors"].append(str(error))
        return result


def latest_water_pair(samples, fallback):
    from water_sample_ranker.domain.measurements import valid_measurement
    from water_sample_ranker.domain.models import SamplePair

    dated = [
        s
        for s in samples
        if s.date and not invalid(s, ("water",)) and valid_measurement(s.water_l_day)
    ]
    if not dated:
        return SamplePair()
    last = max(s.date for s in dated)
    group = [s for s in dated if s.date == last]
    if len({s.water_l_day for s in group}) != 1:
        return SamplePair(reason="SEPARATOR_CONFLICT")
    return SamplePair(last=group[0], evidence=tuple(e for s in group for e in s.evidence))


def run_update(
    config: AppConfig,
    *,
    target_field: str,
    as_of: date | None = None,
    progress: ProgressCallback | None = None,
) -> ServiceResult:
    if not target_field.strip():
        raise ServiceError("Выберите одно месторождение")

    def relay(update: ProgressUpdate) -> None:
        if progress:
            progress(ProgressUpdate(int(update.percent * 0.85), update.stage))

    result = run_validate(config, as_of=as_of, progress=relay)
    if result.has_errors:
        raise ServiceError(result.summary())
    field = normalize_field_name(target_field)
    if field not in result.report["available_fields"]:
        raise ServiceError("Месторождение отсутствует в проверенном фонде")
    return _write_validated(config, result, field, progress)


def asdict_location_group(group: dict[str, Any]) -> dict[str, Any]:
    return group | {"header_locations": [asdict(loc) for loc in group["header_locations"]]}


def _write_validated(
    config: AppConfig, result: ServiceResult, field: str, progress: ProgressCallback | None = None
) -> ServiceResult:
    template = config.path("calculator")
    assert template is not None
    output = (
        config.path("output") or config.root / "outputs" / f"{template.stem}_UPDATED_{field}.xlsx"
    )
    output = output.resolve()
    protected = [config.path(r) for r in ("calculator", *ROLES, *OPTIONAL_ROLES, "manual_input")]
    if output.suffix.lower() != ".xlsx" or output in [p.resolve() for p in protected if p]:
        raise ServiceError("UNSAFE_OUTPUT: результат должен быть отдельным новым XLSX")
    for protected_dir in (
        config.root / "data",
        config.root / "gdis_loader",
        config.root / "templates",
    ):
        if output.is_relative_to(protected_dir.resolve()):
            raise ServiceError("UNSAFE_OUTPUT: каталог источников или шаблонов")
    if any(
        output.is_relative_to(p.resolve()) for p in config.root.glob("gdis_loader*") if p.is_dir()
    ):
        raise ServiceError("UNSAFE_OUTPUT: read-only reference directory")
    report_path = output.with_suffix(".load-report.json")
    log_path = output.with_suffix(".log")
    if any(p.exists() for p in (output, report_path, log_path)):
        raise ServiceError("OUTPUT_EXISTS: выберите новое имя результата")
    result_rows_all = result.rows
    rows = tuple(r for r in result.rows if r.identity.fund.normalized_field == field)
    result.rows = rows
    result.report["selected_field"] = field
    result.report["rows"] = [asdict(r) for r in rows]
    result.report["rows_written"] = len(rows)
    result.report["selected_evidence_stage_b"] = evidence_aggregates(rows)
    result.report["selected_freshness"] = freshness(
        rows, date.fromisoformat(result.report["calculation_date"])
    )
    result.report["monitoring"]["series_register"] = [
        s
        for s in result.report.get("monitoring", {}).get("series_register", [])
        if s["field"] == field
    ]
    day = date.fromisoformat(result.report["calculation_date"])
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, tempname = tempfile.mkstemp(suffix=".xlsx", dir=output.parent)
    os.close(fd)
    tmp = Path(tempname)
    tmp.unlink()
    created: list[OwnedFile] = []
    try:
        write(
            template,
            tmp,
            rows,
            day,
            field,
            int(config.extra.get("top_n", 5)),
            config.criteria,
            manual_input=config.path("manual_input"),
            all_rows=tuple(result_rows_all),
        )
        if progress:
            progress(ProgressUpdate(96, "Проверка перед публикацией новой копии"))
        result.report["output_structure"] = inspect_template(tmp)
        for p, before in result.report.get("operator_manifest", {}).items():
            if fingerprint(Path(p)) != before:
                raise ServiceError("MANUAL_SOURCE_CHANGED")
        result.report["changed_ooxml_parts"] = changed_parts(template, tmp)
        for role, before in result.report["source_manifest"].items():
            p = config.path(role)
            assert p is not None
            if fingerprint(p) != before:
                result.report["source_changed_count"] += 1
                raise ServiceError("SOURCE_CHANGED_DURING_UPDATE: " + role)
        # Same-directory hard link is an atomic no-replace publish on NTFS and Linux.
        if progress:
            progress(ProgressUpdate(97, "Публикация проверенной копии"))
        created.append(link_exclusive(tmp, output))
        result.output = output
        result.report["output"] = fingerprint(output)
        with report_path.open("x", encoding="utf-8") as stream:
            created.append(OwnedFile.from_descriptor(report_path, stream.fileno()))
            json.dump(result.report, stream, ensure_ascii=False, indent=2, default=str)
        with log_path.open("x", encoding="utf-8") as stream:
            created.append(OwnedFile.from_descriptor(log_path, stream.fileno()))
            stream.write(result.summary() + "\n")
            for issue in result.report["issues"]:
                stream.write(f"{issue['code']}: {issue['message']} {issue.get('locations', [])}\n")
            stream.write(
                json.dumps(result.report["fund_reconciliation"], ensure_ascii=False) + "\n"
            )
        if progress:
            progress(ProgressUpdate(100, "Копия калькулятора и отчеты готовы"))
    except BaseException as error:
        for owned in reversed(created):
            owned.remove()
        if not isinstance(error, Exception):
            raise
        raise ServiceError(str(error)) from error
    finally:
        tmp.unlink(missing_ok=True)
    result.owned_files = tuple(created)
    return result


def run_update_all(
    config: AppConfig,
    *,
    as_of: date | None = None,
    output_dir: Path | None = None,
    progress: ProgressCallback | None = None,
) -> tuple[ServiceResult, ...]:
    """Одна загрузка/дата, отдельные книги; при ошибке откат только своих файлов.

    Файлы публикуются no-clobber. Bundle успешен только после возврата функции;
    промежуточный набор при аварийном завершении процесса не является PASS.
    """
    config = deepcopy(config)
    if config.path("output") is not None and output_dir is None:
        raise ServiceError("BATCH_OUTPUT_DIRECTORY_REQUIRED: используйте --output-dir")
    destination = output_dir or config.root / "outputs"
    marker = destination / "bundle-complete.json"
    if marker.exists() or marker.is_symlink():
        raise ServiceError("OUTPUT_EXISTS: bundle-complete.json; нужен новый каталог")

    def relay(update: ProgressUpdate) -> None:
        if progress:
            progress(ProgressUpdate(int(update.percent * 0.70), update.stage))

    checked = run_validate(config, as_of=as_of, progress=relay)
    if checked.has_errors:
        raise ServiceError(checked.summary())
    results: list[ServiceResult] = []
    created: list[OwnedFile] = []
    try:
        for field in checked.report["available_fields"]:
            config.paths["output"] = str(destination / f"Water_Sample_Ranking_{field}.xlsx")
            selected = ServiceResult(deepcopy(checked.report), checked.rows)

            # Preserve global progress and observe cancellation before each publish.
            def checkpoint(update: ProgressUpdate) -> None:
                if progress and update.percent < 100:
                    progress(
                        ProgressUpdate(
                            70 + int(25 * len(results) / len(checked.report["available_fields"])),
                            update.stage,
                        )
                    )

            result = _write_validated(config, selected, field, checkpoint)
            assert result.output is not None
            results.append(result)
            created.extend(result.owned_files)
            if progress:
                progress(
                    ProgressUpdate(
                        70 + int(25 * len(results) / len(checked.report["available_fields"])),
                        "Готово месторождение: " + field,
                    )
                )
        # Final check covers mutation while the last output/report was published.
        for role, before in checked.report["source_manifest"].items():
            p = config.path(role)
            if p is None or fingerprint(p) != before:
                raise ServiceError("SOURCE_CHANGED_DURING_BUNDLE: " + role)
        if progress:
            progress(ProgressUpdate(98, "Проверенный комплект: запись маркера завершения"))
        outputs = []
        for result in results:
            assert result.output is not None
            paths = {
                "workbook": result.output,
                "report": result.output.with_suffix(".load-report.json"),
                "log": result.output.with_suffix(".log"),
            }
            # Flush payloads before publishing the receipt. No partial marker is visible.
            for path in paths.values():
                with path.open("r+b") as stream:
                    os.fsync(stream.fileno())
            outputs.append(
                {
                    "field": result.report["selected_field"],
                    **{role: file_record(path) for role, path in paths.items()},
                }
            )
        payload = {
            "schema": 1,
            "status": "COMPLETE",
            "calculation_date": checked.report["calculation_date"],
            "version": __version__,
            "source_manifest": checked.report["source_manifest"],
            "outputs": outputs,
        }
        created.append(publish_json_exclusive(marker, payload))
        if (
            verify_bundle(
                destination,
                expected_date=checked.report["calculation_date"],
                expected_version=__version__,
                expected_sources=checked.report["source_manifest"],
                expected_fields=checked.report["available_fields"],
            )["status"]
            != "COMPLETE"
        ):
            raise BundleError("BUNDLE_VERIFICATION_FAILED")
        if progress:
            progress(ProgressUpdate(100, "Все месторождения: комплект готов"))
    except BaseException:
        for owned in reversed(created):
            owned.remove()
        raise
    return tuple(results)
