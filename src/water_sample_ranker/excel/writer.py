"""Patch only data/table and run metadata; preserve formula and chart parts byte-for-byte."""

import xml.etree.ElementTree as ET
from copy import deepcopy
from datetime import date, datetime
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from openpyxl.utils import get_column_letter as letter
from openpyxl.utils.datetime import to_excel

from water_sample_ranker.config import CriteriaConfig
from water_sample_ranker.domain.models import DatePrecision, RankedWell
from water_sample_ranker.excel.layout import ALL_DATA_HEADERS, CAPACITY
from water_sample_ranker.excel.manual import (
    identity_key,
    merge_marks,
    read_marks,
    read_operator_settings,
)
from water_sample_ranker.readers.ooxml import Book, q


def row_values(row: RankedWell, as_of: date) -> list[object]:
    identity = row.identity
    last, prev = row.pair.last, row.pair.previous
    factual_criteria = row.factual_criteria or row.criteria
    gate_pair = row.gate_pair or row.pair
    gate = gate_pair.last
    evidence = (
        *row.pair.evidence,
        *row.conclusion_pair.evidence,
        *(row.separator_pair.evidence if row.separator_pair else ()),
        *gate_pair.evidence,
    )
    locations = tuple(
        dict.fromkeys(
            (
                *identity.evidence,
                *(e.location for e in evidence),
                *(loc for c in factual_criteria for loc in c.source_locations),
            )
        )
    )
    origins = "; ".join(f"{Path(loc.file).name}::{loc.sheet}::{loc.cell}" for loc in locations)
    raw_plasts = "; ".join(
        dict.fromkeys(
            str(e.value)
            for e in evidence
            if e.location.cell
            and e.location.cell.rstrip("0123456789") in {"C", "E"}
            and e.unit == ""
            and str(e.value).startswith(("ПК", "АТ", "БТ"))
        )
    )
    return [
        identity.display,
        identity.plast,
        identity.fund.source_category.value,
        identity.fund.normalized_field,
        identity.fund.base_well_id,
        identity.stage.stage_sequence if identity.stage else None,
        identity.status,
        identity.fund.fund_date,
        as_of,
        last.date if last else None,
        prev.date if prev else None,
        row.days_without_sample,
        row.months_without_sample,
        last.chlorine if last else None,
        prev.chlorine if prev else None,
        last.mineralization if last else None,
        prev.mineralization if prev else None,
        last.water_l_day if last else None,
        last.water_l_day / 1000 if last and last.water_l_day is not None else None,
        row.conclusion_pair.last.date if row.conclusion_pair.last else None,
        row.conclusion_pair.last.conclusion if row.conclusion_pair.last else None,
        row.separator,
        row.coverage,
        row.unknown_fraction,
        row.pair.reason,
        identity.fund.raw_display,
        identity.fund.object_name,
        *(c.state.value for c in factual_criteria),
        *(c.diagnostic_reason for c in factual_criteria),
        origins[:32000],
        raw_plasts,
        *diagnostic_values(row),
        stage_start(row),
        identity_key(row),
        (last.date if last else stage_start(row))
        if factual_criteria[0].normalized_score is not None
        else None,
        None,
        None,
        None,
        None,
        None,
        gate.water_l_day if gate else None,
        gate.chlorine if gate else None,
        (gate.date - gate_pair.previous.date).total_seconds() / 86400
        if gate and gate.date and gate_pair.previous and gate_pair.previous.date
        else 0
        if gate
        else None,
        row.criteria[5].diagnostic_reason,
        f"{identity.stage.event_kind.value}; {stage_start(row) or 'дата неполная'}"
        if identity.stage
        else identity.reason,
        row.separator_pair.last.water_l_day
        if row.separator_pair and row.separator_pair.last
        else None,
        row.separator_pair.last.date if row.separator_pair and row.separator_pair.last else None,
    ]


def stage_start(row: RankedWell) -> date | None:
    stage = row.identity.stage
    if (
        not stage
        or row.identity.status != "RESOLVED"
        or stage.started_at.precision != DatePrecision.DAY
    ):
        return None
    d = stage.started_at
    return date(d.year, d.month or 1, d.day or 1)


def set_formula(cell: ET.Element, formula: str) -> None:
    set_value(cell, None)
    ET.SubElement(cell, q("f")).text = formula.lstrip("=")


def diagnostic_values(row: RankedWell) -> list[str]:
    views = {e.number: e for e in row.diagnostic_evidence}
    choke, event, neighbor = (views.get(n) for n in (7, 8, 9))
    pair = choke.pair if choke else None
    text7 = (
        f"{pair.previous.timestamp.isoformat()} → {pair.latest.timestamp.isoformat()}; "
        f"{pair.previous.measurement.value} → {pair.latest.measurement.value} мм; "
        f"Δ={pair.delta}; {pair.direction}; интервал {pair.interval_days} суток; "
        f"исторических изменений-кандидатов: {len(choke.change_candidates) if choke else 0}"
        if pair
        else "Нет сравнимой доверенной пары"
    )
    text8 = (
        "; ".join(
            f"{e.kind} {e.raw_label or ''} "
            f"{e.calendar_interval.lower if e.calendar_interval else '?'}"
            f"…{e.calendar_interval.upper if e.calendar_interval else '?'} "
            f"({e.partial_date.precision.value if e.partial_date else '?'}; {e.date_policy}; "
            f"исходная дата={e.raw_date if e.raw_date is not None else '?'}; "
            f"условная effective_date={e.effective_date or '?'}; "
            f"{e.origin}; {e.cutoff_relation}; {e.stage_relation}; conflict={e.conflict})"
            for e in event.events
        )
        if event
        else "SOURCE_UNAVAILABLE"
    )
    text9 = (
        "; ".join(
            f"{n.neighbor}: куст {n.pad}; {n.plast}; Cl цели {n.target_chlorine.value} "
            f"от {n.target_date.isoformat()}, Cl соседа {n.neighbor_chlorine.value} "
            f"от {n.neighbor_date.isoformat()}; {n.reliability}"
            for n in neighbor.neighbors
        )
        if neighbor
        else "Отложен заказчиком; DISABLED"
    )
    locations = [loc for v in views.values() for loc in v.locations]
    if choke:
        for o in (*choke.observations, *((pair.previous, pair.latest) if pair else ())):
            locations.extend((o.measurement.location, o.header_location, *o.identity_locations))
    if event:
        locations.extend(loc for e in event.events for loc in e.locations)
    if neighbor:
        for n in neighbor.neighbors:
            locations.extend(
                (*n.locations, n.target_chlorine.location, n.neighbor_chlorine.location)
            )
    sources = "; ".join(
        f"{Path(loc.file).name}::{loc.sheet}::{loc.cell}" for loc in dict.fromkeys(locations)
    )
    reasons = "; ".join(f"№{n}: {','.join(v.reasons)}" for n, v in views.items())
    values = [
        text7,
        choke.comparison_status if choke else "RULE_PENDING",
        text8,
        text9 or "Нет подтверждённых кустовых кандидатов; №9 UNKNOWN",
        reasons,
        sources,
    ]
    return [
        v if len(v) <= 32000 else v[:31800] + "… Полное evidence в .load-report.json"
        for v in values
    ]


def set_value(cell: ET.Element, value: object) -> None:
    for child in list(cell):
        cell.remove(child)
    cell.attrib.pop("t", None)
    if value is None:
        return
    if isinstance(value, (datetime, date)):
        value = to_excel(value)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        ET.SubElement(cell, q("v")).text = str(value)
    else:
        cell.set("t", "inlineStr")
        ET.SubElement(ET.SubElement(cell, q("is")), q("t")).text = str(value)


def write(
    template: Path,
    output: Path,
    rows: tuple[RankedWell, ...],
    as_of: date,
    field: str,
    top_n: int = 5,
    criteria: CriteriaConfig | None = None,
    manual_input: Path | None = None,
    all_rows: tuple[RankedWell, ...] | None = None,
) -> None:
    if len(rows) > CAPACITY:
        raise ValueError(f"FORMULA_CAPACITY_EXCEEDED: {len(rows)} > {CAPACITY}")
    marks = merge_marks(all_rows or rows, read_marks(manual_input or template))
    operator_settings = read_operator_settings(manual_input or template, field)
    with Book(template) as book:
        sheetpath = book.sheets["Данные"]
        xml = ET.fromstring(book.zip.read(sheetpath))
        sheetdata = xml.find(q("sheetData"))
        assert sheetdata is not None
        sample = next((r for r in sheetdata if r.get("r") == "7"), None)
        styles = (
            {c.get("r", "").rstrip("0123456789"): c.get("s", "0") for c in sample}
            if sample is not None
            else {}
        )
        for r in list(sheetdata):
            if int(r.attrib["r"]) >= 7:
                sheetdata.remove(r)
        # Compatible v2 adapter: keep A:AU and every formula reference unchanged.
        header = next(r for r in sheetdata if r.get("r") == "6")
        existing = {c.get("r"): c for c in header}
        for col, name in enumerate(ALL_DATA_HEADERS, 1):
            coord = f"{letter(col)}6"
            cell = existing.get(coord)
            if cell is None:
                cell = ET.SubElement(header, q("c"), {"r": coord, "s": header[0].get("s", "0")})
            set_value(cell, name)
        cols = xml.find(q("cols"))
        if cols is not None:
            ET.SubElement(
                cols,
                q("col"),
                {"min": "48", "max": str(len(ALL_DATA_HEADERS)), "width": "48", "customWidth": "1"},
            )
        for number, record in enumerate(rows, 7):
            r = ET.SubElement(sheetdata, q("row"), {"r": str(number)})
            for col, value in enumerate(row_values(record, as_of), 1):
                name = letter(col)
                cell = ET.SubElement(
                    r,
                    q("c"),
                    {"r": f"{name}{number}", "s": styles.get(name, styles.get("AT", "0"))},
                )
                set_value(cell, value)
            by_col = {c.get("r", "").rstrip("0123456789"): c for c in r}
            cols = {h: letter(i) for i, h in enumerate(ALL_DATA_HEADERS, 1)}
            # Единственная дата расчёта и формулы давности работают после ручной правки.
            set_formula(by_col["I"], "Настройки!$B$7")
            set_formula(
                by_col["L"],
                f'IF(AND(ISNUMBER(J{number}),Настройки!$B$7>=INT(J{number})),Настройки!$B$7-INT(J{number}),"")',
            )
            ref = cols["Опорная дата давности"] + str(number)
            set_formula(
                by_col["M"],
                f'IF(AND(ISNUMBER({ref}),Настройки!$B$7>=INT({ref})),(YEAR(Настройки!$B$7)-YEAR({ref}))*12+MONTH(Настройки!$B$7)-MONTH({ref})-IF(DAY(Настройки!$B$7)<MIN(DAY({ref}),DAY(DATE(YEAR(Настройки!$B$7),MONTH(Настройки!$B$7)+1,0))),1,0),"")',
            )
            key = cols["Identity стадии"] + str(number)
            for header, operator in zip(
                (
                    "Ручной ГТМ",
                    "Дата фактического увеличения диаметра",
                    "Диаметр до мм",
                    "Диаметр после мм",
                    "Основание диаметра",
                ),
                (
                    "Отбор пробы после ГТМ",
                    "Дата увеличения диаметра",
                    "Диаметр до мм",
                    "Диаметр после мм",
                    "Основание диаметра",
                ),
                strict=True,
            ):
                lookup = f"INDEX(tblOperator[{operator}],MATCH({key},tblOperator[Ключ],0))"
                set_formula(
                    by_col[cols[header]],
                    f'IFERROR(IF(OR({key}="",ISBLANK({lookup})),"",{lookup}),"")',
                )
        # A one-row blank table remains well-formed for an empty payload.
        end = max(7, 6 + len(rows))
        dimension = xml.find(q("dimension"))
        if dimension is not None:
            dimension.set("ref", f"A1:{letter(len(ALL_DATA_HEADERS))}{end}")
        replacements = {sheetpath: ET.tostring(xml, encoding="utf-8", xml_declaration=True)}
        for name in book.zip.namelist():
            if name.startswith("xl/tables/") and name.endswith(".xml"):
                t = ET.fromstring(book.zip.read(name))
                if t.get("displayName") == "tblData":
                    ref = f"A6:{letter(len(ALL_DATA_HEADERS))}{end}"
                    t.set("ref", ref)
                    filt = t.find(q("autoFilter"))
                    if filt is not None:
                        filt.set("ref", ref)
                    columns = t.find(q("tableColumns"))
                    assert columns is not None
                    for c in list(columns):
                        columns.remove(c)
                    columns.set("count", str(len(ALL_DATA_HEADERS)))
                    for i, header_name in enumerate(ALL_DATA_HEADERS, 1):
                        ET.SubElement(
                            columns, q("tableColumn"), {"id": str(i), "name": header_name}
                        )
                    replacements[name] = ET.tostring(t, encoding="utf-8", xml_declaration=True)
        settingspath = book.sheets["Настройки"]
        settings = ET.fromstring(book.zip.read(settingspath))
        cfg = criteria or CriteriaConfig()
        parameters = {
            "G22": (
                f"{cfg.cl_low};{cfg.cl_high};{cfg.cl_delta};"
                f"{cfg.mineralization_max};{cfg.interval_days}"
            ),
            "G23": f"{cfg.cl_low};{cfg.cl_low};{cfg.interval_days}",
            "G26": f"{cfg.water_l_day};{cfg.cl_low}",
            "B6": "PROVISIONAL; " + cfg.policy_status,
        }
        parameters.update(operator_settings)
        for cell in settings.iter(q("c")):
            if cell.get("r") in parameters:
                set_value(cell, parameters[cell.attrib["r"]])
            elif cell.get("r") == "B7":
                set_value(cell, as_of)
            elif cell.get("r") == "B8":
                set_value(cell, field)
            elif cell.get("r") == "B9":
                set_value(cell, top_n)
        settingsdata = settings.find(q("sheetData"))
        assert settingsdata is not None
        operator_styles = {
            c.get("r", "").rstrip("0123456789"): c.get("s", "0")
            for r in settingsdata
            if r.get("r") == "56"
            for c in r
        }
        for r in list(settingsdata):
            if int(r.get("r", "0")) >= 56:
                settingsdata.remove(r)
        for number, values in enumerate(marks.values(), 56):
            r = ET.SubElement(settingsdata, q("row"), {"r": str(number)})
            for col, value in enumerate(values, 1):
                coord = f"{letter(col)}{number}"
                # Стили защищённой identity / голубого ввода берутся из шаблона.
                c = ET.SubElement(
                    r, q("c"), {"r": coord, "s": operator_styles.get(letter(col), "0")}
                )
                set_value(c, value)
        for name in book.zip.namelist():
            if name.startswith("xl/tables/") and name.endswith(".xml"):
                table = ET.fromstring(book.zip.read(name))
                if table.get("displayName") == "tblOperator":
                    table.set("ref", f"A55:J{max(56, 55 + len(marks))}")
                    af = table.find(q("autoFilter"))
                    if af is not None:
                        af.set("ref", table.get("ref", ""))
                    replacements[name] = ET.tostring(table, encoding="utf-8", xml_declaration=True)
        replacements[settingspath] = ET.tostring(settings, encoding="utf-8", xml_declaration=True)
        # Exclusive creation: never silently replace any existing workbook.
        with output.open("xb") as stream, ZipFile(stream, "w", compression=ZIP_DEFLATED) as target:
            for info in book.zip.infolist():
                target.writestr(
                    deepcopy(info), replacements.get(info.filename, book.zip.read(info.filename))
                )
