"""Локальный приватный register; исходники не изменяются, публикаций нет."""

import argparse
import json
import sys
from collections import Counter
from dataclasses import asdict
from datetime import date
from pathlib import Path

from openpyxl import load_workbook

from water_sample_ranker.config import load_config
from water_sample_ranker.matching.normalize import normalize_field_name, normalize_well_id
from water_sample_ranker.readers.lha import read_lha
from water_sample_ranker.readers.well_history import (
    FIELD_SHEETS,
    find_well_history_schema,
    read_well_history,
)
from water_sample_ranker.service import run_validate
from water_sample_ranker.source_manifest import fingerprint


def decision(row):
    return {
        "identity": row["identity"],
        "database_pair": {k: row["pair"][k] for k in ("reason", "conflict")},
        "slide_pair": {k: row["conclusion_pair"][k] for k in ("reason", "conflict")},
        "criteria": row["criteria"],
        "separator": row["separator"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--as-of", required=True, type=date.fromisoformat)
    parser.add_argument("--baseline", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    from water_sample_ranker.handoff.evidence import aggregate

    target = args.output_dir.resolve()
    if not target.is_relative_to((root / "artifacts/private").resolve()):
        raise ValueError("PRIVATE_DIRECTORY_REQUIRED")
    target.mkdir(exist_ok=False, parents=True)
    config = load_config(root)
    result = run_validate(config, as_of=args.as_of)
    if result.has_errors:
        raise ValueError("VALIDATION_FAILED")
    baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
    history_path, lha_path = config.path("well_history"), config.path("lha")
    assert history_path is not None and lha_path is not None
    histories = tuple(
        h
        for f in result.report["available_fields"]
        for h in read_well_history(history_path, f).histories
    )
    samples, _ = read_lha(lha_path)
    cases = []
    book = load_workbook(history_path, read_only=True, data_only=False)
    try:
        for row in result.rows:
            ident, fund = row.identity, row.identity.fund
            common = {
                "field": fund.normalized_field,
                "number": ident.display,
                "raw_id": fund.raw_display,
                "physical_root": fund.base_well_id,
                "lifecycle": asdict(ident.stage) if ident.stage else None,
                "plast": ident.plast,
                "fund_object": fund.object_name,
                "fund_category": fund.source_category.value,
            }
            if ident.status != "RESOLVED":
                sheet = next(
                    k
                    for k, v in FIELD_SHEETS.items()
                    if normalize_field_name(v) == fund.normalized_field
                )
                ws = book[sheet]
                schema = find_well_history_schema(ws)
                assert schema is not None
                raw_matches = []
                for n, values in enumerate(ws.iter_rows(min_row=3, values_only=True), 3):
                    if (
                        normalize_well_id(values[schema.well_column]).base_well_id
                        == fund.base_well_id
                    ):
                        raw_matches.append({"sheet": sheet, "row": n, "raw_row": values})
                candidates = [
                    asdict(h)
                    for h in histories
                    if h.physical_root == fund.base_well_id
                    and normalize_field_name(h.field_name) == fund.normalized_field
                ]
                kind = (
                    "LIFECYCLE_PLAST_CONFLICT"
                    if ident.status == "AMBIGUOUS"
                    else "HISTORY_ROW_ABSENT"
                    if not raw_matches
                    else "HISTORY_STAGE_MISSING"
                )
                cases.append(
                    {
                        **common,
                        "kind": "IDENTITY",
                        "reason": ident.reason,
                        "status": ident.status,
                        "uncertainty_type": kind,
                        "source": "fund+well_history",
                        "evidence_coordinates": [asdict(e) for e in ident.evidence],
                        "raw_history_matches": raw_matches,
                        "candidate_histories": candidates,
                        "resolution_owner": "CUSTOMER",
                        "parser_defect": "NOT_CONFIRMED_BY_RAW_SOURCE_REVIEW",
                        "issue_class": "SOURCE_CONFLICT",
                        "question_category": "NEED_CURRENT_PLAST_AND_STAGE"
                        if ident.status == "AMBIGUOUS"
                        else "DATA_CONTRACT_HISTORY_ROW"
                        if not raw_matches
                        else "NEED_DATE_AND_PLAST",
                        "required_resolution": "Подтвердить текущий ствол/пласт и границы истории"
                        if ident.status == "AMBIGUOUS"
                        else "Предоставить исходную строку истории, дату ввода и пласт",
                    }
                )
            for source, pair in (("database", row.pair), ("slide", row.conclusion_pair)):
                if pair.conflict:
                    coordinates = {e.location for e in pair.evidence}
                    observations = [
                        asdict(s)
                        for s in samples
                        if s.source == source and any(e.location in coordinates for e in s.evidence)
                    ]
                    cases.append(
                        {
                            **common,
                            "kind": "PAIR",
                            "reason": pair.reason,
                            "uncertainty_type": pair.reason,
                            "source": source,
                            "evidence_coordinates": [asdict(e) for e in coordinates],
                            "observations": observations,
                            "confirmed_last_retained": pair.last is not None,
                            "resolution_owner": "CUSTOMER",
                            "parser_defect": "NOT_CONFIRMED",
                            "issue_class": "SOURCE_CONFLICT",
                            "question_category": "DUPLICATE_TIMESTAMP"
                            if pair.reason == "DUPLICATE_TIMESTAMP_CONFLICT"
                            else "UNDATED_SAMPLE_NEEDS_DATE",
                            "required_resolution": "Подтвердить исследование для спорного timestamp"
                            if pair.reason == "DUPLICATE_TIMESTAMP_CONFLICT"
                            else "Подтвердить дату недатированной пробы",
                            "timestamps": sorted({str(o["date"]) for o in observations}),
                        }
                    )
    finally:
        book.close()

    def key(row):
        f = row["identity"]["fund"]
        return f["normalized_field"], f["raw_display"], f["source_row"], f["source_column"]

    before = {key(r): r for r in baseline["rows"]}
    # Одинаковая JSON-проекция дат/tuple нужна для достоверного baseline comparison.
    current = json.loads(json.dumps([asdict(r) for r in result.rows], default=str))
    after = {key(r): r for r in current}
    changes = []
    for k in before.keys() | after.keys():
        a, b = before.get(k), after.get(k)
        if a is None or b is None:
            changes.append({"key": k, "kind": "MEMBERSHIP_CHANGED"})
        elif decision(a) != decision(b):
            changes.append(
                {
                    "key": k,
                    "before": decision(a),
                    "after": decision(b),
                    "explanation": "SEMANTIC_VALIDATION_OR_HEADER_CONTRACT; REVIEW_REQUIRED",
                }
            )
    summary = aggregate(result)
    summary["discrepancy_review"] = {
        "identity_cases": sum(c["kind"] == "IDENTITY" for c in cases),
        "identity_uncertainties": dict(
            Counter(c["uncertainty_type"] for c in cases if c["kind"] == "IDENTITY")
        ),
        "database_pair_cases": sum(
            c["kind"] == "PAIR" and c["source"] == "database" for c in cases
        ),
        "slide_pair_cases": sum(c["kind"] == "PAIR" and c["source"] == "slide" for c in cases),
        "decision_changes_from_baseline": len(changes),
        "parser_defects_confirmed": 0,
        "customer_source_resolution": "PENDING",
    }
    payloads = {
        "discrepancy-register.json": {"as_of": str(args.as_of), "cases": cases},
        "baseline-differences.json": changes,
        "summary.json": summary,
        "validate.json": result.report,
        "input-hashes.json": result.report["source_manifest"],
        "baseline-binding.json": fingerprint(args.baseline),
        "evidence-register.json": {
            "as_of": str(args.as_of),
            "rows": [
                {
                    "identity": asdict(r.identity),
                    "evidence": [asdict(e) for e in r.diagnostic_evidence],
                }
                for r in result.rows
            ],
            "monitoring_series": result.report.get("monitoring", {}).get("series_register", []),
        },
    }
    for name, payload in payloads.items():
        (target / name).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
