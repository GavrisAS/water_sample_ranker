"""Coverage is evidence completeness, never a surrogate final score."""

from water_sample_ranker.domain.models import CriterionResult, Truth


def coverage(criteria: tuple[CriterionResult, ...]) -> tuple[float | None, float]:
    applicable = sum(c.weight for c in criteria if c.state != Truth.NOT_APPLICABLE)
    known = sum(c.weight for c in criteria if c.state in {Truth.TRUE, Truth.FALSE})
    unknown = sum(c.state == Truth.UNKNOWN for c in criteria) / len(criteria)
    return (known / applicable if applicable else None), unknown
