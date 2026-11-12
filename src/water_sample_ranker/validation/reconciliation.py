"""Independent fund totals, including discrepancies the list parser cannot explain."""

from pathlib import Path

from water_sample_ranker.domain.models import FundParseResult
from water_sample_ranker.readers.common import parse_number
from water_sample_ranker.readers.ooxml import Book, Cell


def reconcile(path: Path, fund: FundParseResult) -> dict[str, object]:
    if not fund.sheet_name:
        return {}
    from openpyxl import load_workbook

    from water_sample_ranker.domain.models import FundCategory
    from water_sample_ranker.readers.fund import category_columns

    book = load_workbook(path, data_only=True, keep_links=False)
    try:
        sheet = book[fund.sheet_name]
        active = [
            col
            for col, cat in category_columns(sheet)
            if cat in {FundCategory.PRODUCING_GAS, FundCategory.STOPPED_IN_REPORTING_MONTH}
        ]
        detail_active = 0
        for row in range(11, sheet.max_row + 1):
            label = str(sheet.cell(row, 2).value or "")
            if label and "ВСЕГО" not in label.upper() and "ИТОГО" not in label.upper():
                detail_active += sum(
                    parse_number(sheet.cell(row, col).value) or 0 for col in active
                )
        active_check = {
            "category_columns": active,
            "detail_active": detail_active,
            "parsed_active": len(fund.wells),
            "detail_matches_lists": detail_active == len(fund.wells),
        }
    finally:
        book.close()
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
        "active_reconciliation": active_check,
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
