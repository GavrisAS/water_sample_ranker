"""Explicit private qualification of all discovered fields; never upload data."""

import argparse
import json
from datetime import date
from pathlib import Path

from openpyxl import load_workbook

from tests.formula_subset import Evaluator
from water_sample_ranker.config import load_config
from water_sample_ranker.domain.scoring import confirmed_score
from water_sample_ranker.excel.layout import DATA_HEADERS
from water_sample_ranker.service import run_update, run_validate
from water_sample_ranker.source_manifest import fingerprint


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    args = parser.parse_args()
    config = load_config()
    target = args.output_dir.resolve()
    target.mkdir(parents=True, exist_ok=False)
    result = run_validate(config, as_of=args.as_of)
    (target / "validate.json").write_text(
        json.dumps(result.report, ensure_ascii=False, indent=2, default=str)
    )
    if result.has_errors:
        raise SystemExit(result.summary())
    evidence = {
        "calculation_date": args.as_of.isoformat(),
        "fields": {},
        "rating_methodology": "PROVISIONAL",
    }
    for field in result.report["available_fields"]:
        config.paths["output"] = str(target / f"Water_UPDATED_{field}.xlsx")
        out = run_update(config, target_field=field, as_of=args.as_of)
        assert out.output is not None
        book = load_workbook(out.output, data_only=False)
        ev = Evaluator(book)
        ev.depth = 1  # Неизменяемая книга: общий кэш независимой проверки.
        headers = {c.value: c.column for c in book["Данные"][6]}
        assert tuple(headers)[: len(DATA_HEADERS)] == DATA_HEADERS
        assert len(out.rows) == book["Данные"].max_row - 6
        for index, row in enumerate(out.rows, 7):
            assert book["Данные"].cell(index, headers["Месторождение"]).value == field
            for criterion in row.criteria:
                assert (
                    book["Данные"].cell(index, headers[f"Критерий {criterion.number}"]).value
                    == criterion.state.value
                )
            excelrow = index - 3
            assert abs(ev.cell("Расчет", f"J{excelrow}") - confirmed_score(row.criteria)) < 1e-11
            calculated = ev.cell("Расчет", f"I{excelrow}")
            assert row.coverage is not None and abs(calculated - row.coverage) < 1e-9
        ordered = sorted(out.rows, key=lambda r: -confirmed_score(r.criteria))
        for n, row in enumerate(ordered, 4):
            score = confirmed_score(row.criteria)
            assert ev.cell("Рейтинг", f"B{n}") == row.identity.display
            assert abs(ev.cell("Рейтинг", f"E{n}") - score) < 1e-11
            assert ev.cell("Рейтинг", f"A{n}") == 1 + sum(
                confirmed_score(r.criteria) > score for r in out.rows
            )
        for name, before in out.report["source_manifest"].items():
            source_path = config.path(name)
            assert source_path is not None
            assert fingerprint(source_path) == before
        evidence["fields"][field] = {
            "rows": len(out.rows),
            "coverage": out.report["coverage"][field],
            "output": out.report["output"],
            "ooxml_parts": out.report["changed_ooxml_parts"],
            "independent_python_formula_subset": "PASS",
            "native_excel": "NOT_RUN",
        }
        book.close()
        print(
            field,
            len(out.rows),
            out.report["coverage"][field]["identity"],
            out.report["output"]["sha256"],
            flush=True,
        )
    (target / "qualification.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2, default=str)
    )


if __name__ == "__main__":
    main()
