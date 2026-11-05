"""Independent fund totals, including discrepancies the list parser cannot explain."""

from pathlib import Path

from water_sample_ranker.domain.models import FundParseResult
from water_sample_ranker.readers.common import parse_number
from water_sample_ranker.readers.ooxml import Book, Cell


def reconcile(path: Path, fund: FundParseResult) -> dict[str, object]:
    if not fund.sheet_name:
        return {}
    details = {col: 0 for col in ("C", "D", "G", "H", "I", "J", "K")}
    subtotals = []
    grand = {}
    with Book(path) as b:
        for row, cells in b.rows(fund.sheet_name):
            label = str(cells.get("B", Cell("")).value or "").upper()
            nums = {c: parse_number(cells.get(c, Cell("")).value) for c in details}
            if "ИТОГО" in label:
                grand = {"row": row, **nums}
            elif "ВСЕГО" in label:
                subtotals.append({"row": row, **nums})
            elif label and any(nums[c] is not None for c in ("C", "D", "G", "H")) and row > 10:
                for c, n in nums.items():
                    details[c] += int(n or 0)
    parsed = {}
    for w in fund.wells:
        parsed[w.normalized_field] = parsed.get(w.normalized_field, 0) + 1
    declared_scope = sum(details[c] for c in ("C", "D", "G", "H"))
    grand_scope = sum(float(grand.get(c) or 0) for c in ("C", "D", "I")) if grand else None
    mismatch = declared_scope != grand_scope or details["K"] != grand.get("K")
    return {
        "sheet": fund.sheet_name,
        "parsed_lists": len(fund.wells),
        "fields": parsed,
        "detail_categories": details,
        "detail_scope_C_D_G_H": declared_scope,
        "grand_C_D_I": grand_scope,
        "grand": grand,
        "subtotals": subtotals,
        "detail_K": details["K"],
        "known_mismatch_observed": mismatch,
        "note": "Детальные категории и списки сравниваются отдельно; категории не исправляются",
    }
