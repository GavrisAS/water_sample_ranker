"""Current history from fund + dated Base; GDI is supporting evidence only."""

from dataclasses import replace
from datetime import date

from water_sample_ranker.domain.models import (
    FundWell,
    IdentityObservation,
    TargetIdentity,
    WellHistory,
)
from water_sample_ranker.matching.lifecycle import resolve_lifecycle_stage


def resolve_current(
    well: FundWell,
    histories: tuple[WellHistory, ...],
    gdi: tuple[IdentityObservation, ...],
    as_of: date,
) -> TargetIdentity:
    resolution = resolve_lifecycle_stage(replace(well, fund_date=as_of), None, histories)
    stage = resolution.stage
    evidence = well.locations
    if stage is not None:
        evidence = (*evidence, stage.source_location)
        supported = tuple(
            r
            for r in gdi
            if r.normalized_field == well.normalized_field
            and r.study_date
            and r.study_date.date() <= as_of
            and resolve_lifecycle_stage(well, r, histories).stage == stage
        )
        evidence = (*evidence, *(loc for r in supported for loc in r.locations))
        return TargetIdentity(
            well,
            stage.target_well_id or well.raw_display,
            stage.plast,
            stage,
            "RESOLVED" if stage.plast else "UNRESOLVED",
            "FUND_BASE_DATED_HISTORY; GDI_SUPPORT_ONLY",
            tuple(dict.fromkeys(evidence)),
        )
    return TargetIdentity(
        well,
        well.target_well_id or well.raw_display,
        well.object_plast_hint,
        None,
        "AMBIGUOUS" if resolution.issue else "UNRESOLVED",
        resolution.issue.message if resolution.issue else "NO_CONFIRMED_LIFECYCLE",
        (*evidence, *(resolution.issue.locations if resolution.issue else ())),
    )
