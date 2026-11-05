"""Conservative assignment of GDI rows to confirmed well-history stages."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum

from water_sample_ranker.domain.dates import (
    effective_calendar_date,
)
from water_sample_ranker.domain.models import (
    DiagnosticIssue,
    FundWell,
    IdentityObservation,
    IssueSeverity,
    PartialDate,
    SourceLocation,
    WellEventKind,
    WellHistory,
    WellStage,
)
from water_sample_ranker.matching.fund_scope import parse_fund_object_scope
from water_sample_ranker.matching.normalize import normalize_field_name, normalize_well_id


class LifecycleStatus(StrEnum):
    RESOLVED = "RESOLVED"
    AMBIGUOUS = "AMBIGUOUS"
    NOT_FOUND = "NOT_FOUND"


@dataclass(frozen=True, slots=True)
class LifecycleResolution:
    """A stage assignment plus any provenance-rich ambiguity diagnostic."""

    stage: WellStage | None
    issue: DiagnosticIssue | None = None

    @property
    def status(self) -> LifecycleStatus:
        if self.stage is not None:
            return LifecycleStatus.RESOLVED
        return LifecycleStatus.AMBIGUOUS if self.issue is not None else LifecycleStatus.NOT_FOUND

    @property
    def identity(self) -> str | None:
        return self.stage.target_well_id if self.stage is not None else None

    @property
    def plast(self) -> str | None:
        return self.stage.plast if self.stage is not None else None


def propagate_stage_identities(history: WellHistory) -> WellHistory:
    """Carry canonical identity through dated boundaries in confirmed source order."""
    stages: list[WellStage] = []
    ordinal = 0
    for stage in history.stages:
        parent = stages[-1] if stages else None
        identity = (
            stage.target_well_id or normalize_well_id(history.source_display_well).target_well_id
        )
        stage_ordinal = None
        if stage.event_kind is WellEventKind.ZBS:
            ordinal += 1
            stage_ordinal = ordinal
            identity = f"{stage.physical_root}_ЗБС" + (str(ordinal) if ordinal > 1 else "")
        elif stage.event_kind in {WellEventKind.PVLG, WellEventKind.PNLG}:
            identity = parent.target_well_id if parent is not None else None
            stage_ordinal = parent.ordinal if parent is not None else None
        stages.append(
            replace(
                stage,
                target_well_id=identity,
                parent_stage_id=parent.stage_sequence if parent else None,
                ordinal=stage_ordinal,
            )
        )
    return replace(history, stages=tuple(stages))


def attach_active_fund_branches(wells: tuple[FundWell, ...]) -> tuple[FundWell, ...]:
    """Retain explicit active sibling identities as a constraint for root-only rows."""
    return tuple(
        replace(
            well,
            active_zbs_identities=tuple(
                sorted(
                    set(well.active_zbs_identities)
                    | {
                        other.target_well_id
                        for other in wells
                        if other.is_rating_eligible
                        and other.base_well_id == well.base_well_id
                        and other.normalized_field == well.normalized_field
                        and other.target_well_id is not None
                        and normalize_well_id(other.target_well_id).recognized_variant == "ЗБС"
                    }
                )
            ),
        )
        for well in wells
    )


def _variant_kind(identity: str) -> str | None:
    return normalize_well_id(identity).recognized_variant


def _zbs_ordinal(identity: str) -> int | None:
    return normalize_well_id(identity).ordinal


def _record_identity(record: IdentityObservation | None, fund_well: FundWell) -> str:
    if record is not None:
        return (
            record.target_well_id
            or normalize_well_id(record.raw_well_name or "").target_well_id
            or ""
        )
    return fund_well.target_well_id or normalize_well_id(fund_well.raw_display).target_well_id or ""


def _stage_kind_for_variant(variant: str | None) -> WellEventKind | None:
    if variant == "ЗБС":
        return WellEventKind.ZBS
    if variant == "ПВЛГ":
        return WellEventKind.PVLG
    if variant == "ПНЛГ":
        return WellEventKind.PNLG
    if variant in {None, "МЗС", "ГС"}:
        return WellEventKind.INITIAL
    return None


def _issue(
    code: str,
    message: str,
    fund_well: FundWell,
    record: IdentityObservation | None,
    stages: tuple[WellStage, ...],
) -> DiagnosticIssue:
    locations = list(fund_well.locations)
    if record is not None and record.source_file and record.source_sheet and record.date_cell:
        locations.append(SourceLocation(record.source_file, record.source_sheet, record.date_cell))
    locations.extend(stage.source_location for stage in stages)
    unique = tuple(dict.fromkeys(locations))
    return DiagnosticIssue(
        code=code,
        severity=IssueSeverity.WARNING,
        message=message,
        source="well_history",
        well_id=fund_well.base_well_id,
        locations=unique,
    )


def _unique_prelaunch_stage(
    well: FundWell, record: IdentityObservation, history: WellHistory
) -> LifecycleResolution:
    """Permit launch disagreement only for a unique identity/plast branch.

    Root labels cannot jump a lifecycle boundary. An explicit ZBS can precede
    its Base launch only after the preceding confirmed history started and
    before any successor with that same display identity. No day tolerance.
    """
    if not isinstance(record, IdentityObservation) or record.study_date is None:
        return LifecycleResolution(None)
    identity = _record_identity(record, well)
    parsed = normalize_well_id(identity)
    if parsed.recognized_variant not in {None, "ЗБС", "ГС", "МЗС"}:
        return LifecycleResolution(None)
    stages = tuple(
        s
        for s in history.stages
        if s.target_well_id == identity and s.plast == record.normalized_plast
    )
    if len(stages) != 1:
        return LifecycleResolution(None)
    selected = stages[0]
    if record.study_date.date() >= effective_calendar_date(selected.started_at):
        return LifecycleResolution(None)
    if parsed.recognized_variant != "ЗБС":
        return LifecycleResolution(selected if len(history.stages) == 1 else None)
    previous = tuple(s for s in history.stages if s.stage_sequence < selected.stage_sequence)
    if not previous:
        return LifecycleResolution(selected if len(history.stages) == 1 else None)
    if record.study_date.date() < max(effective_calendar_date(s.started_at) for s in previous):
        return LifecycleResolution(None)
    return LifecycleResolution(selected)


def _assign_unsuffixed_record_by_timeline(
    fund_well: FundWell,
    record: IdentityObservation,
    history: WellHistory,
) -> LifecycleResolution:
    """Assign a root-only GDI label to one exact-plast stage by its date."""

    if record.study_date is None:
        return LifecycleResolution(None)
    unresolved_events = tuple(
        event
        for event in history.events
        if event.event_kind in {WellEventKind.ZBS, WellEventKind.PVLG, WellEventKind.PNLG}
        and event.event_date is None
    )
    if unresolved_events:
        locations = [*fund_well.locations, *record.locations]
        locations.extend(event.source_location for event in unresolved_events)
        return LifecycleResolution(
            None,
            DiagnosticIssue(
                code="WELL_HISTORY_STAGE_AMBIGUOUS",
                severity=IssueSeverity.WARNING,
                message="Дата подтвержденной lifecycle-границы не распознана.",
                source="well_history",
                well_id=fund_well.base_well_id,
                locations=tuple(dict.fromkeys(locations)),
            ),
        )

    study_day = effective_calendar_date(record.study_date)
    preceding = tuple(
        stage for stage in history.stages if study_day >= effective_calendar_date(stage.started_at)
    )
    if not preceding:
        return _unique_prelaunch_stage(fund_well, record, history)
    latest = tuple(
        stage
        for stage in preceding
        if not any(
            other is not stage
            and effective_calendar_date(other.started_at)
            > effective_calendar_date(stage.started_at)
            for other in preceding
        )
    )
    if len(latest) != 1:
        return LifecycleResolution(
            None,
            _issue(
                "WELL_HISTORY_STAGE_AMBIGUOUS",
                "Для даты root-only ГДИ подходят несколько lifecycle-этапов.",
                fund_well,
                record,
                latest or preceding,
            ),
        )
    stage = latest[0]
    if record.normalized_plast is None or stage.plast != record.normalized_plast:
        return LifecycleResolution(
            None,
            _issue(
                "UNRESOLVED_PLAST_EQUIVALENCE",
                (
                    "Пласт root-only ГДИ не совпадает с пластом последнего этапа; "
                    "эквивалентность не предполагается."
                ),
                fund_well,
                record,
                (stage,),
            ),
        )
    return LifecycleResolution(stage)


def _resolve_current_fund_stage(
    fund_well: FundWell,
    record: IdentityObservation | None,
    history: WellHistory,
    *,
    expected_kind: WellEventKind | None = None,
    zbs_ordinal: int | None = None,
) -> LifecycleResolution:
    """Resolve the fund's current stage from its plast and confirmed timeline."""

    eligible = tuple(
        stage
        for stage in history.stages
        if effective_calendar_date(stage.started_at) <= fund_well.fund_date
    )
    if not eligible:
        return LifecycleResolution(None)

    scope = parse_fund_object_scope(fund_well.object_name)
    if scope.plast_range is None and fund_well.object_plast_hint is not None:
        scope = replace(scope, exact_plast=fund_well.object_plast_hint)
    if zbs_ordinal is not None:
        zbs_stages = tuple(stage for stage in eligible if stage.event_kind is WellEventKind.ZBS)
        if zbs_ordinal > len(zbs_stages):
            return LifecycleResolution(
                None,
                _issue(
                    "WELL_HISTORY_STAGE_NOT_FOUND",
                    "Ordinal ЗБС отсутствует в истории.",
                    fund_well,
                    record,
                    zbs_stages,
                ),
            )
        named_stage = zbs_stages[zbs_ordinal - 1]
        branch_identity = named_stage.target_well_id
        eligible = tuple(
            stage
            for stage in eligible
            if stage == named_stage
            or (
                branch_identity is not None
                and stage.target_well_id == branch_identity
                and stage.stage_sequence >= named_stage.stage_sequence
            )
        )
    if zbs_ordinal is None and len(fund_well.active_zbs_identities) > 1:
        active = tuple(
            stage
            for stage in eligible
            if stage.target_well_id in fund_well.active_zbs_identities
            and scope.compatible(stage.plast)
        )
        if len({stage.target_well_id for stage in active}) > 1:
            return LifecycleResolution(
                None,
                _issue(
                    "WELL_HISTORY_ACTIVE_BRANCH_AMBIGUOUS",
                    "Root-only фонд совместим с несколькими работающими ordinal ЗБС.",
                    fund_well,
                    record,
                    active,
                ),
            )
    latest_date = max(effective_calendar_date(stage.started_at) for stage in eligible)
    latest = tuple(
        stage
        for stage in eligible
        if effective_calendar_date(stage.started_at) == latest_date
        and scope.compatible(stage.plast)
        and (expected_kind is None or zbs_ordinal is not None or stage.event_kind is expected_kind)
    )
    # Same-day sequential boundaries have a confirmed order in the source history.
    if len(latest) > 1 and all(stage.event_kind is WellEventKind.ZBS for stage in latest):
        latest = (max(latest, key=lambda stage: stage.stage_sequence),)

    if len(latest) == 1:
        return LifecycleResolution(latest[0])

    ambiguity_candidates = latest or tuple(
        stage for stage in eligible if effective_calendar_date(stage.started_at) == latest_date
    )
    return LifecycleResolution(
        None,
        _issue(
            "WELL_HISTORY_STAGE_AMBIGUOUS",
            (
                "Текущий пласт фонда не соответствует единственному последнему lifecycle-этапу; "
                "предыдущая история не используется."
                if not latest
                else "Для текущего состояния фонда остаются несколько совместимых этапов истории."
            ),
            fund_well,
            record,
            ambiguity_candidates,
        ),
    )


def resolve_lifecycle_stage(
    fund_well: FundWell,
    record: IdentityObservation | None,
    histories: tuple[WellHistory, ...],
) -> LifecycleResolution:
    """Assign a source identity to one confirmed lifecycle interval.

    The physical root is used only to find candidate history rows. Target
    identity, field, plast, and (when available) the study timestamp must then
    agree before a stage is selected.
    """

    identity = _record_identity(record, fund_well)
    variant = _variant_kind(identity)
    normalized_identity = normalize_well_id(identity)
    matching_histories = tuple(
        history
        for history in histories
        if history.physical_root == fund_well.base_well_id
        and normalize_field_name(history.field_name) == fund_well.normalized_field
    )
    if variant in {"МЗС", "ГС"} or (
        normalized_identity.target_well_id != normalized_identity.base_well_id and variant is None
    ):
        matching_histories = tuple(
            history
            for history in matching_histories
            if normalize_well_id(history.source_display_well).target_well_id
            == normalized_identity.target_well_id
        )
    else:
        root_histories = tuple(
            history
            for history in matching_histories
            if normalize_well_id(history.source_display_well).target_well_id
            == normalized_identity.base_well_id
        )
        if root_histories:
            matching_histories = root_histories
    if not matching_histories:
        return LifecycleResolution(None)
    if len(matching_histories) > 1:
        stages = tuple(stage for history in matching_histories for stage in history.stages)
        issue = _issue(
            "WELL_HISTORY_STAGE_AMBIGUOUS",
            "Для физического корня найдены несколько строк истории в том же месторождении.",
            fund_well,
            record,
            stages,
        )
        return LifecycleResolution(None, issue)

    history = matching_histories[0]
    expected_kind = _stage_kind_for_variant(variant)
    if expected_kind is None and variant not in {"ПВР", "КЛК"}:
        return LifecycleResolution(None)

    plast = record.normalized_plast if record is not None else fund_well.object_plast_hint
    stages = tuple(
        stage
        for stage in history.stages
        if stage.event_kind is expected_kind and (plast is None or stage.plast == plast)
    )
    unresolved_events = tuple(
        event
        for event in history.events
        if event.event_kind in {WellEventKind.ZBS, WellEventKind.PVLG, WellEventKind.PNLG}
        and event.event_date is None
    )
    if unresolved_events:
        locations = list(fund_well.locations)
        if record is not None and record.source_file and record.source_sheet and record.date_cell:
            locations.append(
                SourceLocation(record.source_file, record.source_sheet, record.date_cell)
            )
        locations.extend(event.source_location for event in unresolved_events)
        return LifecycleResolution(
            None,
            DiagnosticIssue(
                code="WELL_HISTORY_STAGE_AMBIGUOUS",
                severity=IssueSeverity.WARNING,
                message="Дата подтвержденной границы жизненного цикла не распознана.",
                source="well_history",
                well_id=fund_well.base_well_id,
                locations=tuple(dict.fromkeys(locations)),
            ),
        )

    if record is None and variant in {None, "ПВЛГ", "ПНЛГ", "ПВР", "КЛК"}:
        return _resolve_current_fund_stage(
            fund_well,
            record,
            history,
            expected_kind=(expected_kind if variant in {"ПВЛГ", "ПНЛГ"} else None),
        )

    if record is None and variant == "ЗБС":
        return _resolve_current_fund_stage(
            fund_well,
            record,
            history,
            expected_kind=WellEventKind.ZBS,
            zbs_ordinal=_zbs_ordinal(identity),
        )

    if variant in {None, "ПВР", "КЛК"} and record is not None and record.study_date is not None:
        return _assign_unsuffixed_record_by_timeline(fund_well, record, history)

    if record is None or record.study_date is None:
        if variant in {"ПВР", "КЛК"}:
            candidates = tuple(
                stage for stage in history.stages if plast is None or stage.plast == plast
            )
            if not candidates:
                return LifecycleResolution(None)
            latest_start = max(
                (
                    stage.started_at.year,
                    stage.started_at.month or 1,
                    stage.started_at.day or 1,
                )
                for stage in candidates
            )
            latest_candidates = tuple(
                stage
                for stage in candidates
                if (
                    stage.started_at.year,
                    stage.started_at.month or 1,
                    stage.started_at.day or 1,
                )
                == latest_start
            )
            if len(latest_candidates) == 1:
                return LifecycleResolution(latest_candidates[0])
            return LifecycleResolution(
                None,
                _issue(
                    "WELL_HISTORY_STAGE_AMBIGUOUS",
                    "Для текущего пласта найдено несколько равнодатных этапов истории.",
                    fund_well,
                    record,
                    latest_candidates,
                ),
            )
        stages = tuple(
            stage
            for stage in history.stages
            if stage.event_kind is expected_kind and (plast is None or stage.plast == plast)
        )
        if variant == "ЗБС":
            all_zbs = tuple(
                sorted(
                    (stage for stage in history.stages if stage.event_kind is WellEventKind.ZBS),
                    key=lambda stage: (
                        stage.started_at.year,
                        stage.started_at.month or 1,
                        stage.started_at.day or 1,
                        stage.stage_sequence,
                    ),
                )
            )
            ordinal = _zbs_ordinal(identity)
            if ordinal is not None and ordinal <= len(all_zbs):
                selected_zbs = all_zbs[ordinal - 1]
                return LifecycleResolution(
                    selected_zbs if plast is None or selected_zbs.plast == plast else None,
                    None
                    if plast is None or selected_zbs.plast == plast
                    else _issue(
                        "UNRESOLVED_PLAST_EQUIVALENCE",
                        "Этап ЗБС имеет другое обозначение текущего пласта.",
                        fund_well,
                        record,
                        (selected_zbs,),
                    ),
                )
        if not stages:
            return LifecycleResolution(None)
        if len(stages) != 1:
            return LifecycleResolution(
                None,
                _issue(
                    "WELL_HISTORY_STAGE_AMBIGUOUS",
                    "Для обозначения скважины подходит несколько событий одного типа.",
                    fund_well,
                    record,
                    stages,
                ),
            )
        return LifecycleResolution(stages[0])

    all_stages = history.stages
    study_day = effective_calendar_date(record.study_date)
    preceding = tuple(
        stage for stage in all_stages if study_day >= effective_calendar_date(stage.started_at)
    )
    if variant == "ЗБС" and _zbs_ordinal(identity) is not None:
        all_zbs = tuple(stage for stage in all_stages if stage.event_kind is WellEventKind.ZBS)
        ordinal = _zbs_ordinal(identity)
        assert ordinal is not None
        if ordinal > len(all_zbs):
            return LifecycleResolution(
                None,
                _issue(
                    "WELL_HISTORY_STAGE_NOT_FOUND",
                    "Ordinal ЗБС отсутствует в истории.",
                    fund_well,
                    record,
                    all_zbs,
                ),
            )
        named = all_zbs[ordinal - 1]
        preceding = tuple(
            stage
            for stage in preceding
            if stage == named
            or (
                named.target_well_id is not None
                and stage.target_well_id == named.target_well_id
                and stage.stage_sequence >= named.stage_sequence
            )
        )
    if not preceding:
        return _unique_prelaunch_stage(fund_well, record, history)
    latest_date = max(effective_calendar_date(stage.started_at) for stage in preceding)
    latest = tuple(
        stage for stage in preceding if effective_calendar_date(stage.started_at) == latest_date
    )
    if len(latest) != 1:
        return LifecycleResolution(
            None,
            _issue(
                "WELL_HISTORY_STAGE_AMBIGUOUS",
                "Несколько подтвержденных этапов могут предшествовать этой записи ГДИ.",
                fund_well,
                record,
                latest,
            ),
        )
    selected = latest[0]
    if variant == "ЗБС" and (
        _zbs_ordinal(identity) is not None
        or normalize_well_id(selected.target_well_id or "").recognized_variant == "ЗБС"
    ):
        expected_kind = selected.event_kind
    if selected.event_kind is not expected_kind or (plast is not None and selected.plast != plast):
        return LifecycleResolution(
            None,
            _issue(
                "WELL_HISTORY_STAGE_AMBIGUOUS",
                "Идентификатор и пласт ГДИ не соответствуют последнему этапу истории.",
                fund_well,
                record,
                (selected,),
            ),
        )
    return LifecycleResolution(selected)


def assign_stage_by_date(
    *,
    physical_root: str,
    normalized_field: str,
    normalized_plast: str,
    observed_at: datetime | PartialDate,
    histories: tuple[WellHistory, ...],
    observation_location: SourceLocation,
    target_identity: str | None = None,
) -> LifecycleResolution:
    """Assign an unsuffixed source row to a unique confirmed lifecycle stage.

    Physical root only locates possible history rows. Field, plast, exact
    observation timestamp, and the confirmed event timeline decide whether a
    stage can be selected. An observation in a partial-date boundary window
    remains unresolved.
    """

    candidates = tuple(
        history
        for history in histories
        if history.physical_root == physical_root
        and normalize_field_name(history.field_name) == normalized_field
        and normalize_well_id(history.source_display_well).target_well_id
        == (
            target_identity
            if target_identity is not None
            and normalize_well_id(target_identity).recognized_variant in {"ГС", "МЗС"}
            else physical_root
        )
    )
    if len(candidates) != 1:
        relevant_stages = tuple(stage for history in candidates for stage in history.stages)
        return LifecycleResolution(
            None,
            _timestamp_issue(
                "Для физического корня не найдена единственная строка истории.",
                observation_location,
                relevant_stages,
            ),
        )

    history = candidates[0]
    observation_date = effective_calendar_date(observed_at)
    unresolved_boundaries = tuple(
        event
        for event in history.events
        if event.event_kind in {WellEventKind.ZBS, WellEventKind.PVLG, WellEventKind.PNLG}
        and event.event_date is None
    )
    all_stages = history.stages
    if (
        target_identity is not None
        and normalize_well_id(target_identity).recognized_variant == "ЗБС"
        and normalize_well_id(target_identity).ordinal is not None
    ):
        all_stages = tuple(stage for stage in all_stages if stage.target_well_id == target_identity)
    preceding = tuple(
        stage
        for stage in all_stages
        if observation_date >= effective_calendar_date(stage.started_at)
    )
    if unresolved_boundaries:
        return LifecycleResolution(
            None,
            _timestamp_issue(
                "Дата одной из подтвержденных границ жизненного цикла не распознана.",
                observation_location,
                preceding,
                tuple(event.source_location for event in unresolved_boundaries),
            ),
        )
    if not preceding:
        return LifecycleResolution(None)
    latest = tuple(
        stage
        for stage in preceding
        if not any(
            other is not stage
            and effective_calendar_date(other.started_at)
            > effective_calendar_date(stage.started_at)
            for other in preceding
        )
    )
    if len(latest) != 1:
        return LifecycleResolution(
            None,
            _timestamp_issue(
                "Для timestamp ЛХА подходят несколько этапов истории.",
                observation_location,
                latest or preceding,
            ),
        )
    selected = latest[0]
    if selected.plast != normalized_plast:
        return LifecycleResolution(None)
    return LifecycleResolution(selected)


def _timestamp_issue(
    message: str,
    observation_location: SourceLocation,
    stages: tuple[WellStage, ...],
    extra_locations: tuple[SourceLocation, ...] = (),
) -> DiagnosticIssue:
    return DiagnosticIssue(
        code="WELL_HISTORY_STAGE_AMBIGUOUS",
        severity=IssueSeverity.WARNING,
        message=message,
        source="well_history",
        locations=tuple(
            dict.fromkeys(
                (
                    observation_location,
                    *(stage.source_location for stage in stages),
                    *extra_locations,
                )
            )
        ),
    )
