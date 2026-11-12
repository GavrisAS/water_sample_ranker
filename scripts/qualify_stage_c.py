"""Независимый пересчёт синтетики в LibreOffice; не native Excel acceptance."""

import argparse
import json
import shutil
import subprocess
import tempfile
from datetime import date
from pathlib import Path

from openpyxl import load_workbook

from scripts.make_synthetic_demo import generate
from water_sample_ranker.domain.scoring import confirmed_score
from water_sample_ranker.service import run_update_all


def qualify(directory: Path) -> dict:
    executable = shutil.which("libreoffice")
    if not executable:
        return {"status": "NOT_RUN", "reason": "LIBREOFFICE_UNAVAILABLE", "native_excel": "NOT_RUN"}
    config = generate(directory / "synthetic", year=2042, extended=True)
    results = run_update_all(config, as_of=date(2042, 11, 5), output_dir=directory / "original")
    originals = []
    for result in results:
        assert result.output is not None
        b = load_workbook(result.output)
        expected = [(r.identity.display, confirmed_score(r.criteria)) for r in result.rows]
        expected.sort(key=lambda x: -x[1])
        originals.append((result.output, expected))
        b.close()

    def recalc(source: Path, label: str) -> Path:
        out = directory / label
        out.mkdir(exist_ok=True)
        profile = directory / ("lo-profile-" + label)
        completed = subprocess.run(
            [
                executable,
                "-env:UserInstallation=" + profile.as_uri(),
                "--headless",
                "--convert-to",
                "xlsx",
                "--outdir",
                str(out),
                str(source),
            ],
            capture_output=True,
            text=True,
            timeout=120,
            check=True,
        )
        target = out / source.name
        if not target.is_file():
            raise ValueError("RECALC_NO_OUTPUT: " + completed.stdout)
        return target

    for index, (path, expected) in enumerate(originals):
        target = recalc(path, f"recalculated-{index}")
        b = load_workbook(target, data_only=True)
        for r, (well, score) in enumerate(expected, 4):
            assert b["Рейтинг"].cell(r, 2).value == well
            assert abs(b["Рейтинг"].cell(r, 5).value - score) < 1e-11
            assert b["Рейтинг"].cell(r, 1).value == 1 + sum(v > score for _, v in expected)
        for s in b:
            for row in s:
                for c in row:
                    assert c.data_type != "e", f"FORMULA_ERROR:{s.title}:{c.coordinate}:{c.value}"
        b.close()
    # Изменения только в ячейках готовой книги, без повторного сервиса/Python-расчёта.
    path = originals[0][0]
    b = load_workbook(path)
    s = b["Настройки"]
    field = s["B8"].value
    row = next(
        r
        for r in range(56, s.max_row + 1)
        if s.cell(r, 2).value == field and s.cell(r, 3).value == "DEMO-ALFA"
    )
    s.cell(row, 6, "Да")
    s["D28"] = 0.23
    edited = directory / "edited.xlsx"
    b.save(edited)
    b.close()
    target = recalc(edited, "edited-recalculated")
    b = load_workbook(target, data_only=True)
    actual = {b["Рейтинг"].cell(r, 2).value: b["Рейтинг"].cell(r, 5).value for r in range(4, 7)}
    original = dict(originals[0][1])
    assert abs(actual["DEMO-ALFA"] - original["DEMO-ALFA"] - 0.23) < 1e-11
    assert abs(actual["DEMO-BETA"] - original["DEMO-BETA"]) < 1e-11
    b.close()
    return {
        "status": "PASS",
        "engine": "LibreOffice",
        "scope": "SYNTHETIC_RECALCULATION",
        "fields": 3,
        "manual_gtm_and_weight_without_loader": "PASS",
        "python_formula_scores_ranks": "PASS",
        "formula_errors": 0,
        "native_excel": "NOT_RUN",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="wsr-stage-c-") as temporary:
        result = qualify(Path(temporary))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
