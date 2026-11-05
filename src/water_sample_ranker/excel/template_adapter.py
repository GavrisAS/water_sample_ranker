"""One-time derivation from an actual root workbook; inputs remain read-only."""

import warnings
from copy import copy
from pathlib import Path

from openpyxl import load_workbook
from openpyxl.cell.cell import MergedCell
from openpyxl.chart.data_source import AxDataSource, NumDataSource, NumRef, StrRef
from openpyxl.chart.series import SeriesLabel
from openpyxl.formatting.formatting import ConditionalFormattingList
from openpyxl.formatting.rule import CellIsRule
from openpyxl.packaging.core import DocumentProperties
from openpyxl.styles import PatternFill, Protection
from openpyxl.utils import get_column_letter as letter
from openpyxl.workbook.properties import CalcProperties
from openpyxl.worksheet.datavalidation import DataValidation, DataValidationList
from openpyxl.worksheet.table import Table, TableStyleInfo

from water_sample_ranker.criteria.rules import NAMES, WEIGHTS
from water_sample_ranker.excel.layout import CAPACITY, DATA_HEADERS, SHEETS, STATUS


def link(header: str, index: int) -> str:
    return (
        f'=IFERROR(IF(ISBLANK(INDEX(tblData[{header}],{index})),"",'
        f'INDEX(tblData[{header}],{index})),"")'
    )


def derive(reference: Path, destination: Path) -> None:
    if destination.resolve() == reference.resolve() or destination.exists():
        raise ValueError("Шаблон должен записываться в новый файл")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        book = load_workbook(reference, keep_links=False)
    if tuple(book.sheetnames) != SHEETS:
        raise ValueError("UX_REFERENCE_AMBIGUOUS: структура требует ручной адаптации")
    chart = book["Рейтинг"]._charts[0] if book["Рейтинг"]._charts else None
    header_style = copy(book["Данные"]["A6"]._style)
    data_style = copy(book["Данные"]["A7"]._style)
    body_style = copy(book["Расчет"]["B4"]._style)
    original_table_styles = {
        t.name: copy(t.tableStyleInfo) for s in book for t in s.tables.values()
    }
    for s in book:
        for row in s:
            for c in row:
                if isinstance(c, MergedCell):
                    continue
                c.value = None
                c.comment = None
                c.hyperlink = None
        s.tables.clear()
        s.data_validations = DataValidationList()
        s.conditional_formatting = ConditionalFormattingList()
        s._charts.clear()
        s._images.clear()
        s.sheet_state = "visible"
        # Original settings contain retired hidden rows; new nine rows must all be visible.
        if s.title == "Настройки":
            for n in range(20, 30):
                s.row_dimensions[n].hidden = False
        s.protection.sheet = False
    book.defined_names.clear()
    book.properties = DocumentProperties(
        creator="Water Sample Ranker",
        title="Рейтинг отбора проб воды",
        description="water-v0.2-stage-A; VISUAL_REVIEW_PENDING",
    )
    book.calculation = CalcProperties(calcMode="auto", fullCalcOnLoad=True, forceFullCalc=True)

    def put(sheet: str, coord: str, value: object) -> None:
        book[sheet][coord] = value

    def table(sheet: str, name: str, row: int, headers: tuple[str, ...], size: int) -> None:
        s = book[sheet]
        for col, title in enumerate(headers, 1):
            c = s.cell(row, col, title)
            c._style = copy(header_style)
        t = Table(displayName=name, ref=f"A{row}:{letter(len(headers))}{row + size}")
        t.tableStyleInfo = original_table_styles.get(name) or TableStyleInfo(
            name="TableStyleMedium2", showRowStripes=True
        )
        s.add_table(t)

    put("README", "A1", "Калькулятор рейтинга отбора проб воды")
    put("README", "A4", "Инструкция — этап A / методика v0.2")
    for col, value in zip("ABC", ("№", "Раздел", "Практическое правило"), strict=True):
        put("README", col + "6", value)
    instructions = (
        (
            "Проверьте файлы",
            "В приложении выберите новый калькулятор, источники и нажмите «Проверить файлы».",
        ),
        (
            "Выберите месторождение",
            "Выберите ровно одно найденное поле. Действующий и бездействующий фонд включены.",
        ),
        (
            "Обновите калькулятор",
            "Проверьте «Критерии…», задайте результат. Приложение создаст новую копию XLSX и "
            "отчёт JSON/log.",
        ),
        (
            "Проверьте признаки",
            "На листе «Критерии» девять состояний TRUE/FALSE/UNKNOWN/NOT_APPLICABLE. UNKNOWN "
            "не означает Нет.",
        ),
        (
            "Проверьте расчет",
            "Дни и полные календарные месяцы — факты. Покрытие — сумма известных применимых "
            "весов / сумма применимых весов.",
        ),
        (
            "Посмотрите рейтинг",
            "Числовой итог и места пусты: функция №1, балльность и агрегирование UNKNOWN не "
            "утверждены. ТОП не заполнен.",
        ),
        (
            "Источники",
            "Данные импортируются из книг; ячейки происхождения, "
            "raw/cache/единицы/диагностика находятся в соседнем JSON.",
        ),
        (
            "Правка данных",
            "Меняйте пути и параметры в приложении и повторяйте обновление. Факты и "
            "состояния в XLSX — один снимок; ручная правка фактов требует повторной проверки.",
        ),
        (
            "Открытые вопросы",
            "№4: scope исключения 23 неизвестен. №7–9: нет подтвержденного окна/плана/карт. "
            "Они UNKNOWN.",
        ),
        (
            "Пересчет",
            "Excel автоматически пересчитывает связанные витрины, покрытие и диаграмму. "
            "Итоговый score/rank заблокирован методикой.",
        ),
        (
            "Нативная проверка",
            "VISUAL_REVIEW_PENDING; требуется Microsoft Excel: открытие без repair, "
            "автопересчет, сохранение и повторное открытие.",
        ),
    )
    for row, (title, text) in enumerate(instructions, 7):
        for col, value in zip("ABC", (row - 6, title, text), strict=True):
            put("README", f"{col}{row}", value)
    settings = book["Настройки"]
    settings["A1"] = "Настройки рейтинга отбора проб воды"
    settings["A3"] = "ОБЩИЕ НАСТРОЙКИ"
    for row, values in enumerate(
        (
            ("Методика", "water-v0.2-stage-A", "Веса из презентации; баллы не утверждены"),
            (
                "Итоговый рейтинг",
                "PENDING",
                "Функция №1 и агрегирование UNKNOWN требуют согласования",
            ),
            ("Дата расчета", None, "Фиксируется при запуске"),
            ("Месторождение", None, "Одно поле на книгу"),
            ("ТОП-N", 5, "График заполняется только при достоверном итоговом балле"),
            ("Статусы", "TRUE/FALSE/UNKNOWN/NOT_APPLICABLE", "UNKNOWN не дает ноль или FALSE"),
            (
                "Политика пары",
                "LATEST_TWO_NO_SKIP",
                "Дубли с различиями блокируют; неполные последние записи не пропускаются",
            ),
            (
                "Исключение №23",
                "SCOPE_PENDING",
                "До подтверждения scope критерий №4 для точного номера 23 UNKNOWN",
            ),
        ),
        5,
    ):
        for col, value in enumerate(values, 1):
            settings.cell(row, col, value)
    settings["B7"].number_format = "dd.mm.yyyy"
    settings["A16"] = "Контроль весов"
    settings["B16"] = "=SUM(D21:D29)"
    settings["A18"] = "Скоринг / UNKNOWN"
    settings["B18"] = "НЕ СОГЛАСОВАН"
    settings["A19"] = "КРИТЕРИИ ОТБОРА ПРОБ ВОДЫ"
    table(
        "Настройки",
        "tblCriteria",
        20,
        (
            "№",
            "Критерий",
            "Метод",
            "Вес",
            "Источник",
            "Оператор",
            "Порог",
            "Единицы",
            "Статус",
            "Комментарий",
            "Политика UNKNOWN",
            "Баллы",
            "Версия",
        ),
        9,
    )
    descriptions = (
        ("ЛХА G", "длительность", "PENDING", "мес.; дни", "Шкала №1 не утверждена"),
        (
            "ЛХА G/W/Z",
            ">; <; >; <; >",
            "2000;8000;2000;22000;7",
            "мг/дм³; дни",
            "Cl_prev (2000,8000); ΔCl>2000; обе M<22000; Δt>7",
        ),
        ("ЛХА G/Z", "<; >=; >", "2000;2000;7", "мг/дм³; дни", "Cl_prev<2000; Cl_last>=2000; Δt>7"),
        (
            "Для слайда AB",
            "Т первым / К+Т",
            "SCOPE 23 PENDING",
            "генезис",
            "Исключение только с подтвержденным scope",
        ),
        (
            "Для слайда AB",
            "содержит",
            "?",
            "генезис",
            "По актуальному заключению; cache/error проверяются",
        ),
        (
            "ЛХА L/Z",
            ">; >",
            "1000;2000",
            "л/сут; мг/дм³",
            "Абсолютные пороги без производной по времени",
        ),
        ("Мониторинг ГСС", "PENDING", "PENDING", "мм", "Окно и 0/66/109 не подтверждены"),
        (
            "История / план ГТМ",
            "PENDING",
            "PENDING",
            "дни",
            "План не предоставлен, окно событий не определено",
        ),
        ("Карты / ЛХА", "PENDING", "PENDING", "м", "Нет карт и согласованного правила соседства"),
    )
    for i, (name, weight, desc) in enumerate(zip(NAMES, WEIGHTS, descriptions, strict=True), 1):
        source, operator, threshold, unit, note = desc
        values = (
            i,
            name,
            "Фактический признак",
            weight,
            source,
            operator,
            threshold,
            unit,
            "PENDING" if i in {1, 7, 8, 9} else "Факты",
            note,
            "UNKNOWN явно",
            "PENDING",
            "v0.2",
        )
        for col, value in enumerate(values, 1):
            c = settings.cell(i + 20, col, value)
            c.protection = Protection(locked=True)
    settings.protection.sheet = True
    settings["B9"].protection = Protection(locked=False)
    for row in range(30, 53):
        settings.row_dimensions[row].hidden = True

    data = book["Данные"]
    data["A1"] = "Данные — отбор проб воды"
    data["A4"] = (
        "Желтые ячейки — импортированные факты. Одна строка — одна текущая идентичность "
        "фонда; детали в JSON."
    )
    table("Данные", "tblData", 6, DATA_HEADERS, 1)
    for col in range(1, len(DATA_HEADERS) + 1):
        data.cell(7, col)._style = copy(data_style)
        data.cell(7, col).number_format = (
            "dd.mm.yyyy hh:mm"
            if col in {8, 9, 10, 11, 20}
            else "0.000"
            if col in {14, 15, 16, 17, 18, 19, 23, 24}
            else "General"
        )
        if col > 25:
            data.column_dimensions[letter(col)].width = 22
    data.freeze_panes = "D7"

    criteria = book["Критерии"]
    criteria["A1"] = "Критерии — фактические состояния по скважинам"
    criteria["A2"] = "Скважина"
    criteria["B2"] = "TRUE / FALSE / UNKNOWN / NOT_APPLICABLE; числовая шкала ожидает согласования"
    for i, name in enumerate(NAMES, 2):
        criteria.cell(5, i, name)
        criteria.cell(6, i, f"=Настройки!D{i + 19}")
    for col, name in enumerate(
        (
            "Скважина",
            *NAMES,
            "Дней без пробы",
            "Месяцев без пробы",
            "Qв л/сут",
            "Сепаратор",
            "Покрытие",
            "Статус",
            "Источник / основание",
        ),
        1,
    ):
        c = criteria.cell(19, col, name)
        c._style = copy(header_style)
    criteria.freeze_panes = "B20"
    calc_headers = (
        "Скважина",
        "Пласт",
        "Состояние",
        "Применимых критериев",
        "Известно критериев",
        "UNKNOWN",
        "TRUE",
        "Известных весов",
        "Покрытие",
        "Итоговый балл",
        "Место",
        "Статус",
        "Комментарий",
        "Фактические признаки",
    )
    table("Расчет", "tblCalculation", 3, calc_headers, CAPACITY)
    book["Расчет"]["A1"] = "Расчет — покрытие и ожидающая согласования методика"
    table(
        "Рейтинг",
        "tblRating",
        3,
        (
            "Место",
            "Скважина",
            "Пласт",
            "Состояние",
            "Итоговый балл",
            "TRUE",
            "Покрытие",
            "Статус",
            "Фактические признаки",
        ),
        CAPACITY,
    )
    book["Рейтинг"]["A1"] = STATUS + ": методика уточняется"
    book["Рейтинг"].column_dimensions["G"].width = 14
    for index in range(1, CAPACITY + 1):
        cr, r = index + 19, index + 3
        criteria.cell(cr, 1, link("Скважина", index))
        for i in range(1, 10):
            criteria.cell(cr, i + 1, link(f"Критерий {i}", index))
        for col, header in enumerate(
            ("Дней без пробы", "Месяцев без пробы", "Qв л/сут", "Сепаратор", "Покрытие"), 11
        ):
            criteria.cell(cr, col, link(header, index))
        criteria.cell(cr, 16, f'=IF(A{cr}="","","SCORING_PENDING")')
        criteria.cell(cr, 17, link("Источники", index))
        calc = book["Расчет"]
        for col, header in enumerate(("Скважина", "Пласт", "Состояние"), 1):
            calc.cell(r, col, link(header, index))
        guard = f'IF(A{r}="","",'
        calc.cell(r, 4, "=" + guard + f'9-COUNTIF(Критерии!B{cr}:J{cr},"NOT_APPLICABLE"))')
        calc.cell(
            r,
            5,
            "="
            + guard
            + f'COUNTIF(Критерии!B{cr}:J{cr},"TRUE")+COUNTIF(Критерии!B{cr}:J{cr},"FALSE"))',
        )
        calc.cell(r, 6, "=" + guard + f'COUNTIF(Критерии!B{cr}:J{cr},"UNKNOWN"))')
        calc.cell(r, 7, "=" + guard + f'COUNTIF(Критерии!B{cr}:J{cr},"TRUE"))')
        terms = "+".join(
            f'IF(OR(Критерии!{letter(i + 1)}{cr}="TRUE",'
            f'Критерии!{letter(i + 1)}{cr}="FALSE"),Настройки!$D${i + 20},0)'
            for i in range(1, 10)
        )
        denom = "+".join(
            f'IF(Критерии!{letter(i + 1)}{cr}="NOT_APPLICABLE",0,Настройки!$D${i + 20})'
            for i in range(1, 10)
        )
        calc.cell(r, 8, "=" + guard + terms + ")")
        calc.cell(r, 9, "=" + guard + f'IFERROR(H{r}/({denom}),""))')
        calc.cell(r, 10, '=""')
        calc.cell(r, 11, '=""')
        calc.cell(r, 12, "=" + guard + '"SCORING_PENDING")')
        calc.cell(r, 13, "=" + guard + '"№1, балльность и агрегирование UNKNOWN не утверждены")')
        calc.cell(r, 14, "=" + guard + f'"TRUE: "&G{r}&"; UNKNOWN: "&F{r})')
        rating = book["Рейтинг"]
        rating.cell(r, 1, '=""')
        for col, source_col in (
            (2, "A"),
            (3, "B"),
            (4, "C"),
            (6, "G"),
            (7, "I"),
            (8, "L"),
            (9, "N"),
        ):
            rating.cell(r, col, f'=IF(Расчет!A{r}="","",Расчет!{source_col}{r})')
        rating.cell(r, 5, '=""')
        for s, maxcol, rr in ((criteria, 17, cr), (calc, 14, r), (rating, 9, r)):
            for col in range(1, maxcol + 1):
                if not s.cell(rr, col).has_style:
                    s.cell(rr, col)._style = copy(body_style)
    for row in range(21, 51):
        book["Рейтинг"].cell(
            row, 11, f'=IF(AND(ISNUMBER(E{row - 17}),ROW()-20<=Настройки!$B$9),B{row - 17},"")'
        )
        book["Рейтинг"].cell(
            row, 12, f'=IF(AND(ISNUMBER(E{row - 17}),ROW()-20<=Настройки!$B$9),E{row - 17},"")'
        )
    book["Рейтинг"]["K20"] = "ТОП-N — ожидает согласования итоговых баллов"
    if chart is not None:
        chart.title = "ТОП-N — только достоверные итоговые баллы"
        for series in chart.series:
            series.cat = AxDataSource(strRef=StrRef(f="'Рейтинг'!$K$21:$K$50"))
            series.val = NumDataSource(numRef=NumRef(f="'Рейтинг'!$L$21:$L$50"))
            series.tx = SeriesLabel(v="Итоговый балл")
        book["Рейтинг"].add_chart(chart)
    for s in (criteria, book["Расчет"], book["Рейтинг"]):
        s.conditional_formatting.add(
            "B20:J1019" if s == criteria else "G4:G1003",
            CellIsRule(
                operator="equal", formula=['"UNKNOWN"'], fill=PatternFill("solid", fgColor="FFF2CC")
            ),
        )
    dv = DataValidation(type="list", formula1='"TRUE,FALSE,UNKNOWN,NOT_APPLICABLE"')
    data.add_data_validation(dv)
    dv.add(f"AB7:AJ{CAPACITY + 6}")
    book["Расчет"].freeze_panes = "D4"
    book["Рейтинг"].freeze_panes = "C4"
    destination.parent.mkdir(parents=True, exist_ok=True)
    book.save(destination)
    book.close()
