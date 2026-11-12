"""Подтверждённая сумма без перенормировки; неизвестный вклад остаётся неизвестным."""

from decimal import ROUND_HALF_UP, Decimal

from water_sample_ranker.domain.models import CriterionResult, Truth


def coverage(criteria: tuple[CriterionResult, ...]) -> tuple[float | None, float]:
    active = [c for c in criteria if c.state not in {Truth.DISABLED, Truth.NOT_APPLICABLE}]
    applicable = sum(c.weight for c in active)
    known = sum(c.weight for c in active if c.normalized_score is not None)
    unknown = sum(c.normalized_score is None for c in active)
    return (known / applicable if applicable else None), unknown / len(active) if active else 0.0


def confirmed_score(criteria: tuple[CriterionResult, ...]) -> float:
    return float(
        Decimal(str(sum(c.weighted_contribution or 0 for c in criteria))).quantize(
            Decimal("0.000000000001"), rounding=ROUND_HALF_UP
        )
    )


def unavailable_weight(criteria: tuple[CriterionResult, ...]) -> float:
    return round(
        sum(c.weight for c in criteria if c.state in {Truth.UNKNOWN, Truth.PENDING_CUSTOMER_RULE}),
        12,
    )
