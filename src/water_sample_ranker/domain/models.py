"""Adapted from gdis_loader by AUTHOR_REDACTED; water fund includes inactive wells."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from water_sample_ranker.domain.evidence import CriterionEvidence


class IssueSeverity(StrEnum):
    """Severity used for diagnostics and source validation."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class FundCategory(StrEnum):
    """Operational fund categories read from columns C, D, G, and H."""

    PRODUCING = "дающие"
    PRODUCING_GAS = "дающие газ"
    STOPPED_IN_REPORTING_MONTH = "остановленные в отчетном месяце"
    INACTIVE_CURRENT_YEAR = "бездействующие текущего года"
    INACTIVE_PREVIOUS_YEARS = "бездействующие прошлых лет"


class DatePrecision(StrEnum):
    """Known calendar precision of a source date."""

    DAY = "day"
    MONTH = "month"
    YEAR = "year"


class WellEventKind(StrEnum):
    """Event labels stored in the well history source."""

    INITIAL = "INITIAL"
    ZBS = "ЗБС"
    PVLG = "ПВЛГ"
    PNLG = "ПНЛГ"
    PVR = "ПВР"
    KLK = "КЛК"
    OTHER = "OTHER"


CellValue = str | int | float | bool | date | datetime | None


@dataclass(frozen=True, slots=True)
class SourceLocation:
    """Structured origin of a value involved in a diagnostic."""

    file: str
    sheet: str | None = None
    cell: str | None = None

    def to_dict(self) -> dict[str, str | None]:
        return {"file": self.file, "sheet": self.sheet, "cell": self.cell}


@dataclass(frozen=True, slots=True)
class PartialDate:
    """Calendar date that retains the precision present in its source cell."""

    year: int
    month: int | None
    day: int | None
    precision: DatePrecision

    def __post_init__(self) -> None:
        if self.year < 1 or self.year > 9999:
            raise ValueError("year must be in the range 1..9999")
        if self.precision is DatePrecision.YEAR and (
            self.month is not None or self.day is not None
        ):
            raise ValueError("YEAR precision cannot contain month or day")
        if self.precision is DatePrecision.MONTH and (self.month is None or self.day is not None):
            raise ValueError("MONTH precision requires a month and no day")
        if self.precision is DatePrecision.DAY and (self.month is None or self.day is None):
            raise ValueError("DAY precision requires month and day")
        if self.month is not None and not 1 <= self.month <= 12:
            raise ValueError("month must be in the range 1..12")
        if self.day is not None:
            date(self.year, self.month or 1, self.day)

    def to_dict(self) -> dict[str, int | str | None]:
        return {
            "year": self.year,
            "month": self.month,
            "day": self.day,
            "precision": self.precision.value,
        }


def source_locations(
    source_file: str, source_sheet: str | None, *cells: str | None
) -> tuple[SourceLocation, ...]:
    """Build immutable locations for the supplied source cells."""

    if not source_file:
        return ()
    return tuple(
        SourceLocation(file=source_file, sheet=source_sheet, cell=cell)
        for cell in cells
        if cell is not None
    )


@dataclass(frozen=True, slots=True)
class DiagnosticIssue:
    """One concise diagnostic item suitable for both logs and JSON."""

    code: str
    severity: IssueSeverity
    message: str
    source: str | None = None
    coordinate: str | None = None
    well_id: str | None = None
    locations: tuple[SourceLocation, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity.value,
            "message": self.message,
            "source": self.source,
            "coordinate": self.coordinate,
            "well_id": self.well_id,
            "locations": [location.to_dict() for location in self.locations],
        }


@dataclass(frozen=True, slots=True)
class FundWell:
    """One well occurrence from the operational fund."""

    raw_display: str
    base_well_id: str | None
    field_name: str
    normalized_field: str
    object_name: str
    source_category: FundCategory
    fund_date: date
    source_row: int
    source_column: str
    target_well_id: str | None = None
    object_plast_hint: str | None = None
    active_zbs_identities: tuple[str, ...] = ()
    source_file: str = ""
    source_sheet: str | None = None
    field_cell: str | None = None
    well_cell: str | None = None
    plast_cell: str | None = None

    @property
    def locations(self) -> tuple[SourceLocation, ...]:
        """Return the fund cells that identify this occurrence."""

        cell = self.well_cell or f"{self.source_column}{self.source_row}"
        return source_locations(
            self.source_file,
            self.source_sheet,
            self.field_cell,
            self.plast_cell,
            cell,
        )

    @property
    def is_rating_eligible(self) -> bool:
        """Include the entire operational fund: producing, stopped and inactive."""

        return self.source_category in {
            FundCategory.PRODUCING,
            FundCategory.PRODUCING_GAS,
            FundCategory.STOPPED_IN_REPORTING_MONTH,
            FundCategory.INACTIVE_CURRENT_YEAR,
            FundCategory.INACTIVE_PREVIOUS_YEARS,
        }


@dataclass(frozen=True, slots=True)
class FundParseResult:
    """Fund wells and diagnostics for the selected dated worksheet."""

    wells: tuple[FundWell, ...]
    fund_date: date | None
    sheet_name: str | None
    issues: tuple[DiagnosticIssue, ...] = ()


@dataclass(frozen=True, slots=True)
class WellEvent:
    """One initial commissioning or GTM event from the well history workbook."""

    field_name: str
    physical_root: str
    source_display_well: str
    plast: str | None
    event_kind: WellEventKind
    event_label: str | None
    event_date: PartialDate | None
    source_location: SourceLocation
    raw_block: tuple[CellValue, ...]
    block_number: int | None = None
    raw_date: CellValue = None
    date_number_format: str = "General"


@dataclass(frozen=True, slots=True)
class WellStage:
    """A lifecycle interval seeded by a confirmed boundary event."""

    field_name: str
    physical_root: str
    source_display_well: str
    target_well_id: str | None
    plast: str | None
    event_kind: WellEventKind
    started_at: PartialDate
    stage_sequence: int
    source_location: SourceLocation
    raw_block: tuple[CellValue, ...]
    parent_stage_id: int | None = None
    ordinal: int | None = None


@dataclass(frozen=True, slots=True)
class WellHistory:
    """All retained stages and source events for one well history row."""

    field_name: str
    physical_root: str
    source_display_well: str
    events: tuple[WellEvent, ...]
    stages: tuple[WellStage, ...]
    pad: str | None = None
    pad_location: SourceLocation | None = None


@dataclass(frozen=True, slots=True)
class WellHistoryParseResult:
    """Parsed history rows, schema provenance, and date/event diagnostics."""

    histories: tuple[WellHistory, ...]
    issues: tuple[DiagnosticIssue, ...] = ()
    event_counts: dict[str, int] = field(default_factory=dict)
    date_precision_counts: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class IdentityObservation:
    """Only identity/date evidence, without imported GDIS eligibility or scoring."""

    raw_well_name: str | None
    target_well_id: str | None
    normalized_field: str | None
    normalized_plast: str | None
    study_date: datetime | None
    source_file: str
    source_sheet: str
    date_cell: str

    @property
    def locations(self) -> tuple[SourceLocation, ...]:
        return (SourceLocation(self.source_file, self.source_sheet, self.date_cell),)


class Truth(StrEnum):
    TRUE = "TRUE"
    FALSE = "FALSE"
    UNKNOWN = "UNKNOWN"
    NOT_APPLICABLE = "NOT_APPLICABLE"


@dataclass(frozen=True, slots=True)
class Evidence:
    location: SourceLocation
    raw: CellValue
    value: CellValue
    unit: str = ""
    status: str = "VALID"
    formula: str | None = None
    date_precision: str | None = None
    measurement_field: str | None = None


@dataclass(frozen=True, slots=True)
class Sample:
    field: str
    well: str
    root: str
    plast: str | None
    date: datetime | None
    chlorine: float | None
    mineralization: float | None
    water_l_day: float | None
    conclusion: str | None
    evidence: tuple[Evidence, ...]
    source: str = "database"
    invalid_measurements: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SamplePair:
    """last is retained only if its timestamp group is unique; conflict may be previous-only."""

    last: Sample | None = None
    previous: Sample | None = None
    reason: str = "NO_SAMPLE"
    conflict: bool = False
    evidence: tuple[Evidence, ...] = ()


@dataclass(frozen=True, slots=True)
class CriterionResult:
    number: int
    weight: float
    state: Truth
    raw_value: CellValue = None
    source_date: datetime | None = None
    normalized_score: float | None = None
    weighted_contribution: float | None = None
    source_locations: tuple[SourceLocation, ...] = ()
    diagnostic_reason: str = ""
    policy_version: str = "water-v0.2-stage-A"


@dataclass(frozen=True, slots=True)
class TargetIdentity:
    fund: FundWell
    display: str
    plast: str | None
    stage: WellStage | None
    status: str
    reason: str
    evidence: tuple[SourceLocation, ...]


@dataclass(frozen=True, slots=True)
class RankedWell:
    identity: TargetIdentity
    pair: SamplePair
    conclusion_pair: SamplePair
    criteria: tuple[CriterionResult, ...]
    days_without_sample: int | None
    months_without_sample: int | None
    separator: str
    coverage: float | None
    unknown_fraction: float
    diagnostic_evidence: tuple[CriterionEvidence, ...] = ()
