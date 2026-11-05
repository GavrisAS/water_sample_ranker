"""Literal water criteria. Truth is separate from unapproved numerical scoring."""

import re
from datetime import date

from water_sample_ranker.config import CriteriaConfig
from water_sample_ranker.domain.measurements import valid_measurement
from water_sample_ranker.domain.models import (
    CriterionResult,
    Sample,
    SamplePair,
    TargetIdentity,
    Truth,
)

WEIGHTS = (0.20, 0.08, 0.12, 0.15, 0.15, 0.10, 0.05, 0.10, 0.05)
NAMES = (
    "Продолжительность без отбора проб",
    "Изменение хлора",
    "Изменение генезиса",
    "Наличие технической жидкости",
    "Спорный генезис",
    "Дебит воды и хлор",
    "Увеличение штуцера",
    "Запланировано или проведено ГТМ",
    "Влияние окружения",
)


def invalid(sample: Sample, fields: tuple[str, ...]) -> bool:
    values = {
        "chlorine": sample.chlorine,
        "mineralization": sample.mineralization,
        "water": sample.water_l_day,
    }
    return any(
        name in sample.invalid_measurements
        or (values[name] is not None and not valid_measurement(values[name]))
        for name in fields
    )


def result(
    number: int, state: Truth, pair: SamplePair, reason: str, raw: object = None
) -> CriterionResult:
    return CriterionResult(
        number,
        WEIGHTS[number - 1],
        state,
        raw_value=raw if isinstance(raw, (str, int, float, bool)) else None,
        source_date=pair.last.date if pair.last else None,
        source_locations=tuple(dict.fromkeys(e.location for e in pair.evidence)),
        diagnostic_reason=reason,
    )


def chlorine(
    pair: SamplePair, number: int, config: CriteriaConfig | None = None
) -> CriterionResult:
    cfg = config or CriteriaConfig()
    a, b = pair.last, pair.previous
    if pair.conflict or not a or not b or not a.date or not b.date:
        return result(number, Truth.UNKNOWN, pair, pair.reason)
    required = ("chlorine", "mineralization") if number == 2 else ("chlorine",)
    if any(invalid(s, required) for s in (a, b)):
        return result(number, Truth.UNKNOWN, pair, "INVALID_MEASUREMENT")
    values = (
        (a.chlorine, b.chlorine, a.mineralization, b.mineralization)
        if number == 2
        else (a.chlorine, b.chlorine)
    )
    if any(v is None for v in values):
        return result(number, Truth.UNKNOWN, pair, "INCOMPLETE_LATEST_PAIR_NO_SKIP")
    assert a.chlorine is not None and b.chlorine is not None
    interval = (a.date - b.date).total_seconds() / 86400
    truth = interval > cfg.interval_days
    if number == 2:
        assert a.mineralization is not None and b.mineralization is not None
        truth = (
            truth
            and cfg.cl_low < b.chlorine < cfg.cl_high
            and a.chlorine - b.chlorine > cfg.cl_delta
            and a.mineralization < cfg.mineralization_max
            and b.mineralization < cfg.mineralization_max
        )
    else:
        truth = truth and b.chlorine < cfg.cl_low and a.chlorine >= cfg.cl_low
    return result(
        number,
        Truth.TRUE if truth else Truth.FALSE,
        pair,
        cfg.policy_status,
        a.chlorine - b.chlorine,
    )


def technical(
    pair: SamplePair,
    *,
    well: str,
    field: str = "",
    confirmed_scope: tuple[str, str, str] | None = None,
) -> CriterionResult:
    s = pair.last
    if not s or s.conclusion is None:
        return result(
            4, Truth.UNKNOWN, pair, pair.reason if pair.conflict else "CONCLUSION_UNAVAILABLE"
        )
    if well == "23":
        if confirmed_scope == (field, well, s.plast):
            return result(
                4, Truth.NOT_APPLICABLE, pair, "CONFIRMED_SCOPED_EXCEPTION_23", s.conclusion
            )
        if field == "береговое":
            return result(4, Truth.UNKNOWN, pair, "EXCEPTION_23_SCOPE_UNCONFIRMED", s.conclusion)
    text = re.sub(r"\s+", "", s.conclusion.upper())
    # Exact components, not substring matching of arbitrary prose.
    if re.fullmatch(r"[КПТ](?:\+[КПТ])*\??", text) is None:
        return result(4, Truth.UNKNOWN, pair, "UNCONFIRMED_GENESIS_NOTATION", s.conclusion)
    components = text.rstrip("?").split("+")
    if components[:2] == ["К", "Т"] and len(components) > 2:
        return result(4, Truth.UNKNOWN, pair, "COMPOSITE_K_T_PENDING", s.conclusion)
    yes = components[0] == "Т" or components == ["К", "Т"]
    return result(
        4, Truth.TRUE if yes else Truth.FALSE, pair, "FIRST_COMPONENT_T_OR_EXACT_K_T", s.conclusion
    )


def disputed(pair: SamplePair) -> CriterionResult:
    if not pair.last or pair.last.conclusion is None:
        return result(5, Truth.UNKNOWN, pair, "CONCLUSION_UNAVAILABLE")
    text = pair.last.conclusion
    return result(
        5, Truth.TRUE if "?" in text else Truth.FALSE, pair, "QUESTION_MARK_IN_LATEST_AB", text
    )


def water_rate(pair: SamplePair, config: CriteriaConfig | None = None) -> CriterionResult:
    cfg = config or CriteriaConfig()
    s = pair.last
    if s and invalid(s, ("water", "chlorine")):
        return result(6, Truth.UNKNOWN, pair, "INVALID_MEASUREMENT")
    if not s or s.water_l_day is None or s.chlorine is None:
        return result(6, Truth.UNKNOWN, pair, "LATEST_WATER_OR_CL_UNAVAILABLE")
    return result(
        6,
        Truth.TRUE if s.water_l_day > cfg.water_l_day and s.chlorine > cfg.cl_low else Truth.FALSE,
        pair,
        "ABSOLUTE_THRESHOLD_NO_TIME_DERIVATIVE; " + cfg.policy_status,
        s.water_l_day,
    )


def duration(pair: SamplePair, as_of: date) -> tuple[CriterionResult, int | None, int | None]:
    if not pair.last or not pair.last.date:
        return (
            result(1, Truth.UNKNOWN, pair, "NO_CONFIRMED_LAST_SAMPLE; NO_MAXIMUM_ASSUMED"),
            None,
            None,
        )
    last = pair.last.date.date()
    days = (as_of - last).days
    months = (as_of.year - last.year) * 12 + as_of.month - last.month - (as_of.day < last.day)
    return (
        result(1, Truth.UNKNOWN, pair, "DURATION_OBSERVED; SCORE_FUNCTION_PENDING", days),
        days,
        months,
    )


def separator(pair: SamplePair, plast: str | None, config: CriteriaConfig | None = None) -> str:
    cfg = config or CriteriaConfig()
    if plast is None:
        return "UNKNOWN"
    if plast != "ПК1":
        return "NOT_APPLICABLE"
    if (
        not pair.last
        or invalid(pair.last, ("water",))
        or not valid_measurement(pair.last.water_l_day)
    ):
        return "UNKNOWN"
    assert pair.last.water_l_day is not None
    return "сепаратор МЗС" if pair.last.water_l_day / 1000 >= cfg.separator_m3_day else "Надым"


def evaluate(
    identity: TargetIdentity,
    pair: SamplePair,
    slide: SamplePair,
    as_of: date,
    config: CriteriaConfig | None = None,
) -> tuple[tuple[CriterionResult, ...], int | None, int | None]:
    first, days, months = duration(pair, as_of)
    items = (
        first,
        chlorine(pair, 2, config),
        chlorine(pair, 3, config),
        technical(slide, well=identity.display, field=identity.fund.normalized_field),
        disputed(slide),
        water_rate(pair, config),
        result(7, Truth.UNKNOWN, SamplePair(), "CHOKE_WINDOW_AND_SOURCE_SEMANTICS_PENDING"),
        result(8, Truth.UNKNOWN, SamplePair(), "GTM_PLAN_AND_EVENT_WINDOW_UNAVAILABLE"),
        result(9, Truth.UNKNOWN, pair, "MAPS_NEIGHBOR_RULE_UNAVAILABLE"),
    )
    return items, days, months
