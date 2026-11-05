"""Факты и кандидаты Stage B. Эти типы не расширяют и не присваивают Truth."""

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum

from water_sample_ranker.domain.dates import CalendarInterval
from water_sample_ranker.domain.models import CellValue, Evidence, PartialDate, SourceLocation


class Reason(StrEnum):
    SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"
    IDENTITY_UNRESOLVED = "IDENTITY_UNRESOLVED"
    SCHEMA_DRIFT = "SCHEMA_DRIFT"
    ANOMALOUS_SERIES = "ANOMALOUS_SERIES"
    SUSPECT_VALUE_SEMANTICS = "SUSPECT_VALUE_SEMANTICS"
    UNCONFIRMED_WINDOW = "UNCONFIRMED_WINDOW"
    RULE_PENDING = "RULE_PENDING"
    NO_CONFIRMED_PAD = "NO_CONFIRMED_PAD"
    SOURCE_CONFLICT = "SOURCE_CONFLICT"
    INCOMPLETE_LATEST_NO_SKIP = "INCOMPLETE_LATEST_NO_SKIP"


@dataclass(frozen=True, slots=True)
class ParameterObservation:
    field: str
    display_well: str
    gsk: str | None
    pad: str | None
    raw_object: str | None
    plast: str | None
    timestamp: datetime
    measurement: Evidence
    header_location: SourceLocation
    identity_locations: tuple[SourceLocation, ...]
    quality: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ObservationPair:
    previous: ParameterObservation
    latest: ParameterObservation
    delta: float
    direction: str
    interval_days: float
    quality: str = "OBSERVATION_ONLY"


@dataclass(frozen=True, slots=True)
class TimelineEvent:
    kind: str
    raw_label: str | None
    partial_date: PartialDate | None
    effective_date: date | None
    date_policy: str
    cutoff_relation: str
    stage_sequence: int | None
    stage_relation: str
    plast: str | None
    locations: tuple[SourceLocation, ...]
    origin: str = "HISTORY_FACT"
    conflict: bool = False
    calendar_interval: CalendarInterval | None = None
    raw_date: CellValue = None
    date_number_format: str = "General"


@dataclass(frozen=True, slots=True)
class NeighborCandidate:
    neighbor: str
    pad: str
    plast: str
    target_chlorine: Evidence
    neighbor_chlorine: Evidence
    target_date: datetime
    neighbor_date: datetime
    locations: tuple[SourceLocation, ...]
    colocation: str = "SAME_CONFIRMED_PAD"
    reliability: str = "DIAGNOSTIC_ONLY_NO_DISTANCE_OR_CONNECTIVITY"


@dataclass(frozen=True, slots=True)
class CriterionEvidence:
    number: int
    reasons: tuple[str, ...]
    observations: tuple[ParameterObservation, ...] = ()
    pair: ObservationPair | None = None
    change_candidates: tuple[ObservationPair, ...] = ()
    events: tuple[TimelineEvent, ...] = ()
    neighbors: tuple[NeighborCandidate, ...] = ()
    locations: tuple[SourceLocation, ...] = ()
    # Последняя доверенная пара до барьера остаётся наблюдением, не текущим событием.
    comparison_status: str = "RULE_PENDING"
    optional_source_status: str = "SOURCE_UNAVAILABLE"
