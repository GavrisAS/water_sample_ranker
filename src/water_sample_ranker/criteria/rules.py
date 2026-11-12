"""Literal water criteria. Truth is separate from unapproved numerical scoring."""

import calendar
import re
from dataclasses import replace
from datetime import date

from water_sample_ranker.config import CriteriaConfig
from water_sample_ranker.domain.measurements import valid_measurement
from water_sample_ranker.domain.models import (
    CriterionResult,
    DatePrecision,
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
    "Отбор пробы после ГТМ",
    "Отложен заказчиком",
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
        normalized_score=1.0 if state == Truth.TRUE else 0.0 if state == Truth.FALSE else None,
        weighted_contribution=WEIGHTS[number - 1]
        if state == Truth.TRUE
        else 0.0
        if state == Truth.FALSE
        else None,
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
    if (
        (field, well) == ("береговое", "23")
        and confirmed_scope == ("береговое", "23", "ПК1")
        and (s is None or s.plast == "ПК1")
    ):
        return result(
            4,
            Truth.NOT_APPLICABLE,
            pair,
            "CONFIRMED_SCOPED_EXCEPTION_23",
            s.conclusion if s else None,
        )
    if not s or s.conclusion is None or not s.conclusion.strip():
        return result(
            4, Truth.UNKNOWN, pair, pair.reason if pair.conflict else "CONCLUSION_UNAVAILABLE"
        )
    text = re.sub(r"\s+", "", s.conclusion.upper())
    if re.match(r"^Т(?:\+|\?|$)", text):
        return result(4, Truth.TRUE, pair, "FIRST_COMPONENT_T", s.conclusion)
    # Exact components, not substring matching of arbitrary prose.
    if re.fullmatch(r"[КПТ](?:\+[КПТ])*\??", text) is None:
        return result(4, Truth.UNKNOWN, pair, "UNCONFIRMED_GENESIS_NOTATION", s.conclusion)
    components = text.rstrip("?").split("+")
    yes = components[0] == "Т" or components == ["К", "Т"]
    return result(
        4, Truth.TRUE if yes else Truth.FALSE, pair, "FIRST_COMPONENT_T_OR_EXACT_K_T", s.conclusion
    )


def disputed(pair: SamplePair) -> CriterionResult:
    if not pair.last or not pair.last.conclusion or not pair.last.conclusion.strip():
        return result(5, Truth.UNKNOWN, pair, "CONCLUSION_UNAVAILABLE")
    text = pair.last.conclusion
    return result(
        5, Truth.TRUE if "?" in text else Truth.FALSE, pair, "QUESTION_MARK_IN_LATEST_AB", text
    )


def water_rate(pair: SamplePair, config: CriteriaConfig | None = None) -> CriterionResult:
    cfg = config or CriteriaConfig()
    s = pair.last
    if s and invalid(s, ("water", "chlorine")):
        return result(6, Truth.UNKNOWN, pair, "GATE_UNKNOWN_INVALID_MEASUREMENT")
    if not s or s.water_l_day is None or s.chlorine is None:
        return result(6, Truth.UNKNOWN, pair, "GATE_UNKNOWN_LATEST_WATER_OR_CL_UNAVAILABLE")
    passed = s.water_l_day > cfg.water_l_day and s.chlorine > cfg.cl_low
    return result(
        6,
        Truth.PENDING_CUSTOMER_RULE if passed else Truth.FALSE,
        pair,
        "GATE_PASS_SECOND_STEP_PENDING" if passed else "GATE_FAIL",
        s.water_l_day,
    )


def full_months(start: date, end: date) -> int:
    """Полные месяцы с календарным ограничением дня последним днём месяца."""
    return (
        (end.year - start.year) * 12
        + end.month
        - start.month
        - (end.day < min(start.day, calendar.monthrange(end.year, end.month)[1]))
    )


def duration(
    pair: SamplePair, as_of: date, identity: TargetIdentity | None = None
) -> tuple[CriterionResult, int | None, int | None]:
    last = pair.last.date.date() if pair.last and pair.last.date else None
    reason = "CALENDAR_MONTHS_CLAMPED_PROVISIONAL_LAST_SAMPLE"
    if last is None:
        if pair.conflict or (identity is not None and identity.status != "RESOLVED"):
            return result(1, Truth.UNKNOWN, pair, "STAGE_OR_SAMPLE_CONFLICT"), None, None
        stage = identity.stage if identity else None
        if stage is None or stage.started_at.precision != DatePrecision.DAY:
            return (
                result(1, Truth.UNKNOWN, pair, "CURRENT_STAGE_START_UNAVAILABLE_OR_PARTIAL"),
                None,
                None,
            )
        d = stage.started_at
        last = date(d.year, d.month or 1, d.day or 1)
        reason = "CALENDAR_MONTHS_CLAMPED_PROVISIONAL_CURRENT_STAGE_START"
    if last > as_of:
        return result(1, Truth.UNKNOWN, pair, "FUTURE_REFERENCE_DATE"), None, None
    days, months = (as_of - last).days, full_months(last, as_of)
    score = min(months, 12) / 12
    c = result(1, Truth.TRUE if score > 0 else Truth.FALSE, pair, reason, months)
    return (
        replace(c, normalized_score=score, weighted_contribution=score * WEIGHTS[0]),
        days,
        months,
    )


def separator(pair: SamplePair, plast: str | None, config: CriteriaConfig | None = None) -> str:
    cfg = config or CriteriaConfig()
    if plast is None:
        return "нет данных"
    if plast != "ПК1":
        return "NOT_APPLICABLE"
    if (
        not pair.last
        or invalid(pair.last, ("water",))
        or not valid_measurement(pair.last.water_l_day)
    ):
        return "нет данных"
    assert pair.last.water_l_day is not None
    return "сепаратор МЗС" if pair.last.water_l_day / 1000 >= cfg.separator_m3_day else "Надым"


def evaluate(
    identity: TargetIdentity,
    pair: SamplePair,
    slide: SamplePair,
    as_of: date,
    config: CriteriaConfig | None = None,
) -> tuple[tuple[CriterionResult, ...], int | None, int | None]:
    first, days, months = duration(pair, as_of, identity)
    items = (
        first,
        chlorine(pair, 2, config),
        chlorine(pair, 3, config),
        technical(
            slide,
            well=identity.display,
            field=identity.fund.normalized_field,
            confirmed_scope=(identity.fund.normalized_field, identity.display, identity.plast)
            if identity.status == "RESOLVED" and identity.plast
            else None,
        ),
        disputed(slide),
        water_rate(pair, config),
        result(7, Truth.UNKNOWN, SamplePair(), "CONFIRMED_DIAMETER_INCREASE_AFTER_SAMPLE_REQUIRED"),
        result(8, Truth.UNKNOWN, SamplePair(), "MANUAL_GTM_UNSET"),
        result(9, Truth.DISABLED, SamplePair(), "DEFERRED_BY_CUSTOMER_NO_WEIGHT_REDISTRIBUTION"),
    )
    return items, days, months
