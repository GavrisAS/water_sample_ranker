"""Private audit plus sanitized structural metadata; no production rows in docs."""

import json
import warnings
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from zipfile import ZipFile

from openpyxl import load_workbook

from water_sample_ranker.source_manifest import fingerprint


def audit(path: Path) -> dict:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        w = load_workbook(path, keep_links=False)
    result = {
        "file": path.name,
        "sha256": fingerprint(path)["sha256"],
        "size": path.stat().st_size,
        "calculation": str(w.calculation),
        "names": {k: str(v) for k, v in w.defined_names.items()},
        "sheet_order": w.sheetnames,
        "sheets": {},
    }
    for s in w:
        result["sheets"][s.title] = {
            "dimension": s.calculate_dimension(),
            "state": s.sheet_state,
            "protection": str(s.protection),
            "merges": str(s.merged_cells),
            "freeze": str(s.freeze_panes),
            "column_dimensions": {
                k: {"width": v.width, "hidden": v.hidden, "min": v.min, "max": v.max}
                for k, v in s.column_dimensions.items()
            },
            "row_dimensions": {
                str(k): {"height": v.height, "hidden": v.hidden}
                for k, v in s.row_dimensions.items()
            },
            "tables": {
                t.name: {
                    "ref": t.ref,
                    "style": str(t.tableStyleInfo),
                    "headers": [c.name for c in t.tableColumns],
                }
                for t in s.tables.values()
            },
            "data_validation": str(s.data_validations),
            "conditional_formatting": len(s.conditional_formatting),
            "formula_count": sum(c.data_type == "f" for row in s for c in row),
            "comments": sum(c.comment is not None for row in s for c in row),
            "styles": dict(Counter(c.style_id for row in s for c in row if c.value is not None)),
            "print": {
                "area": str(s.print_area),
                "setup": str(s.page_setup),
                "margins": str(s.page_margins),
                "options": str(s.print_options),
            },
            "charts": [
                {
                    "anchor": str(c.anchor),
                    "series": [
                        {
                            "category": series.cat.strRef.f
                            if series.cat and series.cat.strRef
                            else None,
                            "value": series.val.numRef.f
                            if series.val and series.val.numRef
                            else None,
                        }
                        for series in c.series
                    ],
                }
                for c in s._charts
            ],
        }
    w.close()
    with ZipFile(path) as z:
        styles = ET.fromstring(z.read("xl/styles.xml"))
        result["style_components"] = {
            e.tag.rsplit("}", 1)[-1]: ET.tostring(e, encoding="unicode")
            for e in styles
            if e.tag.rsplit("}", 1)[-1] in {"fonts", "fills", "borders", "numFmts"}
        }
    return result


if __name__ == "__main__":
    ref = audit(Path("GDIS_Rating_Calculator_STRUCTURAL_v3.xlsx"))
    new = audit(Path("templates/Water_Sample_Ranking_Calculator.xlsx"))
    result = {
        "timestamp": datetime.now(UTC).isoformat(),
        "reference": ref,
        "water": new,
        "status": "STRUCTURE_CHECKED / VISUAL_REVIEW_PENDING",
    }
    Path("docs/EXCEL_STRUCTURE.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    with (
        ZipFile("GDIS_Rating_Calculator_STRUCTURAL_v3.xlsx") as a,
        ZipFile("templates/Water_Sample_Ranking_Calculator.xlsx") as b,
    ):
        comparison = {
            "theme_byte_identical": a.read("xl/theme/theme1.xml") == b.read("xl/theme/theme1.xml")
        }
    comparison.update(
        {
            k: ref["style_components"][k] == new["style_components"][k]
            for k in ("fonts", "fills", "borders")
        }
    )
    Path("artifacts/private/style-comparison.json").write_text(json.dumps(comparison, indent=2))
    print("Reference:", ref["size"], ref["sha256"])
    print("Water:", new["size"], new["sha256"])
    print("Style components:", comparison)
    print("Formula counts:", {k: v["formula_count"] for k, v in new["sheets"].items()})
