from pathlib import Path

from openpyxl import Workbook

from water_sample_ranker.readers.monitoring import diagnose


def test_mass_zeros_and_sentinel_strings_never_become_growth(tmp_path: Path) -> None:
    p = tmp_path / "gss.xlsx"
    w = Workbook()
    s = w.active
    s.title = "Техрежим скважин"
    s["F3"] = 46305
    for r in range(4, 122):
        s.cell(r, 1, "Типоразмер штуцера, мм")
        s.cell(r, 4, str(r))
        s.cell(r, 6, 0)
        s.cell(r, 7, "66")
        s.cell(r, 8, 109)
    w.save(p)
    r = diagnose(p)
    assert r["criterion_7"] == "UNKNOWN"
    assert r["choke_rows"] == 118
    assert r["zero_by_column"]["F"] == 118
    assert r["sentinel_frequencies"] == {"0": 118, "66": 118, "109": 118}
