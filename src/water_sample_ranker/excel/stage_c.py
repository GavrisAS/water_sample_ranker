"""Формульный контракт Stage C: шесть листов, редактирование без загрузчика."""

from openpyxl.styles import PatternFill, Protection
from openpyxl.utils import get_column_letter as letter
from openpyxl.worksheet.datavalidation import DataValidation, DataValidationList
from openpyxl.worksheet.table import Table

from water_sample_ranker.excel.layout import ALL_DATA_HEADERS, CAPACITY, STATUS
from water_sample_ranker.excel.manual import OPERATOR_HEADERS


def adapt(book) -> None:
    data, settings, criteria, calc, rating = (
        book[n] for n in ("Данные", "Настройки", "Критерии", "Расчет", "Рейтинг")
    )
    blue = PatternFill("solid", fgColor="DDEBF7")
    settings["B5"] = "water-stage-C-provisional"
    settings["B6"] = "PROVISIONAL; частично согласована"
    settings["C6"] = "Подтверждённая сумма без перенормировки; №6 ожидает второй шаг"
    settings["C7"] = "Дата редактируется; выборка источников зафиксирована при загрузке"
    settings["B10"] = "TRUE / FALSE / UNKNOWN / NOT_APPLICABLE / DISABLED / PENDING_CUSTOMER_RULE"
    settings["B12"] = "Точное исключение по подтверждённой identity"
    settings["A13"] = "Режим №1"
    settings["B13"] = "Полные календарные месяцы"
    settings["C13"] = "Предварительно: день ограничен последним днём месяца; насыщение 12"
    settings["B16"] = "=SUM(D21:D29)"
    settings["A17"] = "Сумма активных весов (не перенормируется)"
    settings["B17"] = '=SUMPRODUCT(D21:D28,--(I21:I28="Да"))'
    settings["B18"] = "Предварительная сумма; ничья — одинаковое место (competition)"
    settings["I20"] = "Включён"
    settings["L20"] = "Шкала"
    for n in range(1, 10):
        r = n + 20
        settings.cell(r, 9, "Да" if n < 9 else "DISABLED")
        settings.cell(r, 12, "0…1" if n == 1 else "PENDING" if n == 6 else "0 / 1")
        settings.cell(r, 13, "Stage C / предварительно")
        settings.cell(r, 11, "Неопределённый вклад не начисляется и показывается отдельно")
        if n < 9:
            for col in (4, 9):
                settings.cell(r, col).protection = Protection(locked=False)
                settings.cell(r, col).fill = blue
    settings["J21"] = "Полные календарные месяцы / 12; не более 1. Метод дробления предварительный"
    settings["J24"] = "Т первым компонентом или именно К+Т; постоянное точное исключение"
    settings["J26"] = "Только gate: FAIL=0; PASS=PENDING_CUSTOMER_RULE без полного веса"
    settings["E28"] = "Ручная таблица на этом листе ниже"
    settings["J28"] = "Нужен отбор после ГТМ: Да / Нет / Не задано; даты плана не нужны"
    settings["J29"] = "Отложен заказчиком; вес не перераспределяется"
    settings.row_dimensions[29].hidden = True
    settings["A53"] = "ВВОД ОПЕРАТОРА: голубые ячейки; ключ и стадия защищены"
    settings["A54"] = (
        "ГТМ: нужен отбор пробы после ГТМ. Для №7: дата, фактические диаметры в мм и основание."
    )
    for col, h in enumerate(OPERATOR_HEADERS, 1):
        settings.cell(55, col, h)
    settings.add_table(Table(displayName="tblOperator", ref="A55:J56"))
    for row in range(56, 56 + CAPACITY):
        for col in range(6, 11):
            settings.cell(row, col).protection = Protection(locked=False)
            settings.cell(row, col).fill = blue
        settings.cell(row, 7).number_format = "dd.mm.yyyy"
    settings.data_validations = DataValidationList()
    for formula, cells in [
        ('"Да,Нет"', "I21:I28"),
        ('"Полные календарные месяцы,Не подтверждено"', "B13"),
        ('"Да,Нет,Не задано"', f"F56:F{55 + CAPACITY}"),
    ]:
        dv = DataValidation(type="list", formula1=formula, allow_blank=True)
        dv.errorTitle, dv.error, dv.showErrorMessage = (
            "Недопустимое значение",
            "Выберите значение из списка",
            True,
        )
        settings.add_data_validation(dv)
        dv.add(cells)
    for cells, low, high, kind in [
        ("D21:D28", "0", "1", "decimal"),
        ("B7", "1", "2958465", "date"),
        (f"H56:I{55 + CAPACITY}", "0.001", "999.999", "decimal"),
    ]:
        dv = DataValidation(
            type=kind, operator="between", formula1=low, formula2=high, allow_blank=True
        )
        dv.showErrorMessage = True
        settings.add_data_validation(dv)
        dv.add(cells)
    for c in ("B7", "B13"):
        settings[c].protection = Protection(locked=False)
        settings[c].fill = blue
    for col, h in enumerate(ALL_DATA_HEADERS, 1):
        data.cell(6, col, h)
    data.tables["tblData"].ref = f"A6:{letter(len(ALL_DATA_HEADERS))}7"
    data.tables["tblData"].tableColumns = []
    data.tables["tblData"]._initialise_columns()
    for col, h in zip(data.tables["tblData"].tableColumns, ALL_DATA_HEADERS, strict=True):
        col.name = h
    for h in (
        "Начало актуальной стадии",
        "Опорная дата давности",
        "Дата фактического увеличения диаметра",
    ):
        data.cell(7, ALL_DATA_HEADERS.index(h) + 1).number_format = "dd.mm.yyyy hh:mm"
    data.data_validations = DataValidationList()
    data.protection.sheet = True
    data.protection.autoFilter = False
    data["A4"] = (
        "Импортированные факты и формулы защищены. Ручной ввод — Настройки, таблица ниже строки 55."
    )
    cols = {h: letter(i) for i, h in enumerate(ALL_DATA_HEADERS, 1)}

    def d(h, r):
        return f"Данные!{cols[h]}{r}"

    calc_headers = (
        [calc.cell(3, c).value for c in range(1, 15)]
        + [f"Вклад {i}" for i in range(1, 10)]
        + ["Недоступный вес", "Известный вес", "Ничья", "Порядок отображения"]
    )
    calc_headers[3:9] = [
        "Активных критериев",
        "Известно критериев",
        "Неопределённых критериев",
        "TRUE",
        "Известных весов",
        "Покрытие",
    ]
    calc_headers[9] = "Предварительный балл"
    for c, h in enumerate(calc_headers, 1):
        calc.cell(3, c, h)
    calc.tables["tblCalculation"].ref = f"A3:AA{CAPACITY + 3}"
    calc.tables["tblCalculation"].tableColumns = []
    calc.tables["tblCalculation"]._initialise_columns()
    for c, h in zip(calc.tables["tblCalculation"].tableColumns, calc_headers, strict=True):
        c.name = h
    rating_headers = [rating.cell(3, c).value for c in range(1, 10)] + [
        "Месторождение",
        "Актуальная стадия",
        "Последняя проба",
        "Дней без пробы",
        "Полных месяцев",
        "Cl мг/дм³",
        "Минерализация мг/дм³",
        "Qв л/сут",
        "Сепаратор",
        "Недоступный вес",
        "Ничья",
        *[f"Вклад {i}" for i in range(1, 9)],
        *[f"Основание {i}" for i in range(1, 9)],
        *[f"Вес {i}" for i in range(1, 9)],
    ]
    for c, h in enumerate(rating_headers, 1):
        rating.cell(3, c, h)
    rating.tables["tblRating"].ref = f"A3:AR{CAPACITY + 3}"
    rating.tables["tblRating"].tableColumns = []
    rating.tables["tblRating"]._initialise_columns()
    for c, h in zip(rating.tables["tblRating"].tableColumns, rating_headers, strict=True):
        c.name = h
    # Диаграмма сохраняется отдельно справа, таблица содержит весь фонд.
    for s in (calc, rating):
        s["A1"] = STATUS
    criteria["A1"] = "Критерии: значения, веса и причины неопределённости"
    criteria["B2"] = "№6: PASS — ожидание правила; №9 отложен. Ручной ввод — Настройки!F56:J1055"
    for i in range(1, 10):
        criteria.cell(19, 17 + i, f"Основание {i}")
    for idx in range(1, CAPACITY + 1):
        cr, r, dr = idx + 19, idx + 3, idx + 6
        guard = f'IF(A{cr}="","",'
        months = d("Месяцев без пробы", dr)
        last = d("Последняя проба", dr)
        for i in range(1, 10):
            imported = d(f"Критерий {i}", dr)
            if i == 1:
                expr = f'IF(Настройки!$B$13<>"Полные календарные месяцы","PENDING_CUSTOMER_RULE",IF(ISNUMBER({months}),IF({months}>0,"TRUE","FALSE"),"UNKNOWN"))'  # noqa: E501 — формула Excel
            elif i == 8:
                manual = d("Ручной ГТМ", dr)
                expr = f'IF({manual}="Да","TRUE",IF({manual}="Нет","FALSE","UNKNOWN"))'
            elif i == 7:
                dt, before, after, basis = [
                    d(h, dr)
                    for h in (
                        "Дата фактического увеличения диаметра",
                        "Диаметр до мм",
                        "Диаметр после мм",
                        "Основание диаметра",
                    )
                ]
                expr = f'IF(AND(ISNUMBER({last}),ISNUMBER({dt}),ISNUMBER({before}),ISNUMBER({after}),{basis}<>""),IF(AND({before}>0,{after}>{before},{dt}>{last},INT({dt})<=Настройки!$B$7),"TRUE","UNKNOWN"),"UNKNOWN")'  # noqa: E501 — формула Excel
            elif i == 9:
                expr = '"DISABLED"'
            else:
                source = d("Дата заключения", dr) if i in (4, 5) else last
                expr = (
                    f'IF(AND(ISNUMBER({source}),INT({source})>Настройки!$B$7),"UNKNOWN",{imported})'
                )
            if i < 9:
                expr = f'IF(Настройки!$I${i + 20}<>"Да","DISABLED",{expr})'
            criteria.cell(cr, i + 1, "=" + guard + expr + ")")
            reason = d(f"Основание {i}", dr)
            if i == 8:
                reason = '"Ручной выбор: Да / Нет / Не задано; пусто = UNKNOWN"'
            if i == 7:
                reason = (
                    '"Нужны подтверждённые диаметры в мм, дата после пробы и основание'
                    '; проценты не диаметр"'
                )
            criteria.cell(cr, 17 + i, "=" + guard + reason + ")")
        for c, h in enumerate(("Дней без пробы", "Месяцев без пробы"), 11):
            criteria.cell(cr, c, "=" + guard + d(h, dr) + ")")
        criteria.cell(cr, 16, "=" + guard + '"PROVISIONAL")')
        cg = f'IF(A{r}="","",'
        calc.cell(
            r,
            4,
            "="
            + cg
            + f'9-COUNTIF(Критерии!B{cr}:J{cr},"DISABLED")-COUNTIF(Критерии!B{cr}:J{cr},"NOT_APPLICABLE"))',  # noqa: E501 — формула Excel
        )
        calc.cell(
            r,
            5,
            "="
            + cg
            + f'COUNTIF(Критерии!B{cr}:J{cr},"TRUE")+COUNTIF(Критерии!B{cr}:J{cr},"FALSE"))',
        )
        calc.cell(
            r,
            6,
            "="
            + cg
            + f'COUNTIF(Критерии!B{cr}:J{cr},"UNKNOWN")+COUNTIF(Критерии!B{cr}:J{cr},"PENDING_CUSTOMER_RULE"))',  # noqa: E501 — формула Excel
        )
        calc.cell(r, 7, "=" + cg + f'COUNTIF(Критерии!B{cr}:J{cr},"TRUE"))')
        for i in range(1, 10):
            st = f"Критерии!{letter(i + 1)}{cr}"
            value = f"MIN({months},12)/12" if i == 1 else "1"
            calc.cell(
                r,
                14 + i,
                "=" + cg + f'IF({st}="TRUE",{value}*Настройки!$D${i + 20},IF({st}="FALSE",0,"")))',
            )
        calc.cell(r, 10, "=" + cg + f"ROUND(SUM(O{r}:W{r}),12))")
        calc.cell(r, 11, "=" + cg + f'1+COUNTIF($J$4:$J${CAPACITY + 3},">"&J{r}))')
        calc.cell(r, 12, "=" + cg + '"PROVISIONAL")')
        calc.cell(
            r,
            13,
            "="
            + cg
            + (
                '"Сумма известных вкладов; №1 предварительно; №6 второе правило ож'
                'идается; №9 отложен")'
            ),
        )
        calc.cell(r, 14, "=" + cg + f'"Неопределённых: "&F{r})')
        unknowns = "+".join(
            f'IF(OR(Критерии!{letter(i + 1)}{cr}="UNKNOWN",Критерии!{letter(i + 1)}{cr}="PENDING_CUSTOMER_RULE"),Настройки!$D${i + 20},0)'  # noqa: E501 — формула Excel
            for i in range(1, 9)
        )
        calc.cell(r, 24, "=" + cg + unknowns + ")")
        known = "+".join(
            f'IF(OR(Критерии!{letter(i + 1)}{cr}="TRUE",Критерии!{letter(i + 1)}{cr}="FALSE"),Настройки!$D${i + 20},0)'  # noqa: E501 — формула Excel
            for i in range(1, 9)
        )
        calc.cell(r, 8, "=" + cg + known + ")")
        calc.cell(r, 9, "=" + cg + f'IFERROR(H{r}/(H{r}+X{r}),""))')
        calc.cell(r, 25, "=" + cg + f"H{r})")
        calc.cell(r, 26, "=" + cg + f'IF(COUNTIF($J$4:$J${CAPACITY + 3},J{r})>1,"Ничья",""))')
        calc.cell(r, 27, "=" + cg + f"K{r}+COUNTIF($J$4:J{r},J{r})-1)")
        # Полный рейтинг сортируется по баллу; равенство не меняет предметного места.
        for c, source_col in (
            (1, "K"),
            (2, "A"),
            (3, "B"),
            (4, "C"),
            (5, "J"),
            (6, "G"),
            (7, "I"),
            (8, "L"),
            (9, "N"),
        ):
            rating.cell(
                r,
                c,
                f'=IFERROR(INDEX(Расчет!${source_col}$4:${source_col}${CAPACITY + 3},MATCH(ROW()-3,Расчет!$AA$4:$AA${CAPACITY + 3},0)),"")',  # noqa: E501 — формула Excel
            )
        position = f"MATCH(ROW()-3,Расчет!$AA$4:$AA${CAPACITY + 3},0)"
        for col, h in enumerate(
            (
                "Месторождение",
                "Стадия актуальная",
                "Последняя проба",
                "Дней без пробы",
                "Месяцев без пробы",
                "Cl последний мг/дм³",
                "Минерализация последняя мг/дм³",
                "Qв л/сут",
                "Сепаратор",
            ),
            10,
        ):
            lookup = f"INDEX(tblData[{h}],{position})"
            rating.cell(r, col, f'=IFERROR(IF(ISBLANK({lookup}),"",{lookup}),"")')
        rating.cell(r, 12).number_format = "dd.mm.yyyy hh:mm"
        for col, source in ((19, "X"), (20, "Z")):
            rating.cell(
                r,
                col,
                f'=IFERROR(INDEX(Расчет!${source}$4:${source}${CAPACITY + 3},{position}),"")',
            )
        for i in range(1, 9):
            source = letter(14 + i)
            lookup = f"INDEX(Расчет!${source}$4:${source}${CAPACITY + 3},{position})"
            rating.cell(r, 20 + i, f'=IFERROR(IF({lookup}="","",{lookup}),"")')
            source = letter(17 + i)
            rating.cell(
                r,
                28 + i,
                f'=IFERROR(INDEX(Критерии!${source}$20:${source}${CAPACITY + 19},{position}),"")',
            )
            rating.cell(r, 36 + i, f'=IF(B{r}="","",Настройки!$D${20 + i})')
    rating["E3"] = "Предварительный балл"
    for row in range(20, 51):
        rating.cell(row, 11).value = None
        rating.cell(row, 12).value = None
    # Восстановить столбцы таблицы, затронутые старой диаграммой.
    for r in range(20, 51):
        position = f"MATCH(ROW()-3,Расчет!$AA$4:$AA${CAPACITY + 3},0)"
        for col, h in ((11, "Стадия актуальная"), (12, "Последняя проба")):
            lookup = f"INDEX(tblData[{h}],{position})"
            rating.cell(r, col, f'=IFERROR(IF(ISBLANK({lookup}),"",{lookup}),"")')
    rating["BA20"] = "Диаграмма; полный фонд слева"
    for row in range(21, 51):
        rating.cell(
            row, 53, f'=IF(AND(ISNUMBER(E{row - 17}),ROW()-20<=Настройки!$B$9),B{row - 17},"")'
        )
        rating.cell(
            row, 54, f'=IF(AND(ISNUMBER(E{row - 17}),ROW()-20<=Настройки!$B$9),E{row - 17},"")'
        )
    if rating._charts:
        from openpyxl.chart.data_source import AxDataSource, NumDataSource, NumRef, StrRef

        chart = rating._charts[0]
        chart.title = "Предварительный рейтинг"
        for series in chart.series:
            series.cat = AxDataSource(strRef=StrRef(f="'Рейтинг'!$BA$21:$BA$50"))
            series.val = NumDataSource(numRef=NumRef(f="'Рейтинг'!$BB$21:$BB$50"))
        chart.anchor = "BA3"
    for s in (criteria, calc, rating):
        s.protection.sheet = True
        s.protection.autoFilter = False
        s.protection.sort = False
    notes = (
        ("Статус", STATUS),
        ("Фонд", "Только дающие газ и остановленные в отчётном периоде; все строки сохраняются."),
        (
            "Ручные ГТМ",
            (
                "На листе Настройки ниже строки 55 выберите Да / Нет / Не задано. "
                "Пересчёт автоматический."
            ),
        ),
        ("Веса", "Голубые ячейки D21:D28 и I21:I28. Вес №9 отложен и не перераспределяется."),
        (
            "Обновление",
            (
                "Выберите предыдущую книгу в поле ручных отметок или как калькулят"
                "ор; новый результат сохраняется отдельно."
            ),
        ),
        (
            "Стадии",
            (
                "Отметки привязаны к полю, скважине, стадии и пласту. Старые стади"
                "и сохраняются в ручной таблице без переноса."
            ),
        ),
        (
            "№1",
            (
                "Предварительные полные календарные месяцы / 12; опора — последняя"
                " проба либо точная дата запуска текущей стадии."
            ),
        ),
        (
            "№6",
            (
                "Входное условие PASS не даёт вес: ожидается правило роста. FAIL д"
                "аёт подтверждённый ноль."
            ),
        ),
        (
            "Неопределённость",
            (
                "UNKNOWN и PENDING не дают подтверждённого вклада; их вес виден от"
                "дельно, без перенормировки."
            ),
        ),
        (
            "Дата",
            (
                "Настройки!B7 — единая дата. Изменение пересчитывает давность; для"
                " другого снимка истории повторите загрузку с --as-of."
            ),
        ),
        (
            "Приёмка",
            (
                "Native Microsoft Excel и визуальная Windows-приёмка требуют факти"
                "ческого выполнения человеком."
            ),
        ),
    )
    for r, (title, text) in enumerate(notes, 7):
        book["README"].cell(r, 2, title)
        book["README"].cell(r, 3, text)
    book["README"]["A4"] = STATUS
