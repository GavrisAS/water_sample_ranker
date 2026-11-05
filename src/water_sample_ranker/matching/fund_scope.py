"""Fund object constraints, distinct from exact research plast semantics."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from water_sample_ranker.matching.normalize import normalize_plast


@dataclass(frozen=True, slots=True)
class FundObjectScope:
    raw_value: str
    exact_plast: str | None = None
    plast_range: tuple[int, int] | None = None
    family: str = "ПК"

    def compatible(self, plast: str | None) -> bool:
        if self.plast_range is not None:
            if (
                plast is None
                or re.fullmatch(re.escape(self.family) + r"\d+(?:\+\d+)*", plast) is None
            ):
                return False
            lower, upper = self.plast_range
            return all(lower <= int(part) <= upper for part in plast[len(self.family) :].split("+"))
        return self.exact_plast is None or self.exact_plast == plast


def parse_fund_object_scope(value: str) -> FundObjectScope:
    """Interpret a parenthesized ПК range only in the fund object context."""
    text = unicodedata.normalize("NFKC", value).upper()
    text = re.sub(r"[–—−]", "-", text)
    match = re.search(r"\(([^()]*)\)", text)
    if match is None:
        return FundObjectScope(value)
    candidate = re.sub(r"\s+", "", match.group(1))
    bounds = re.fullmatch(r"(ПК|АТ|БТ)(\d+)-(\d+)", candidate)
    if bounds is not None:
        family, low, high = bounds.groups()
        lower, upper = int(low), int(high)
        if lower <= upper:
            return FundObjectScope(value, plast_range=(lower, upper), family=family)
        if family == "ПК":
            return FundObjectScope(value)
    plast = normalize_plast(candidate)
    if plast is None or re.fullmatch(r"(?:ПК|АТ|БТ)\d+(?:[+\-]\d+)*", plast) is None:
        return FundObjectScope(value)
    return FundObjectScope(value, exact_plast=plast)
