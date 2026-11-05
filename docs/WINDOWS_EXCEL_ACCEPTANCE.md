# Windows / native Excel / ручная UX приёмка

TECHNICAL PREVIEW — НЕ УТВЕРЖДЁННЫЙ РЕЙТИНГ.
Native Microsoft Excel и человеческая визуальная приёмка: NOT_RUN/PENDING.
Автоматические Windows checks/build/smoke оцениваются по фактическому Actions run
и BUILD_EVIDENCE.json конкретного релиза. Наличие Windows runner не означает Excel.

Пользователь распаковывает весь Windows x64 ZIP и запускает exe без Python/uv.
Рядом обязательны `_internal/`, `templates/`, config.example.toml и русская
инструкция первого запуска. SHA-256 сверяется по соседнему .sha256.

Разработчик Windows может воспроизвести автоматические gates из чистого source SHA:

```powershell
uv python install 3.14
uv sync --locked
./scripts/build_windows.ps1 -BuildVersion 0.1.1 -SourceSHA (git rev-parse HEAD) -PublicRelease
```

Скрипт проверяет источники, запускает полный test suite, создаёт новый isolated
PyInstaller onedir, проверяет runtime/GUI/package version и source SHA, встроенный
шаблон и frozen CLI validate/update/update-all на DEMO. Он не публикует Release.
Старый dist не используется. `--smoke-test` и Qt offscreen — технические gates.

Для ручной приёмки нужен настоящий Windows 10/11 x64 с Microsoft Excel desktop.
Используйте только DEMO до отдельного разрешённого приватного производственного
прогона; выбирайте новые имена выходов. Зафиксируйте source/tag/ZIP SHA,
Windows/Excel version, дату, проверяющего и PASS/FAIL/NOT_RUN по каждой строке:

| Проверка | Статус до отдельной приёмки |
|---|---|
| Запуск на машине без Python; полная папка onedir | NOT_RUN |
| Привычная последовательность выбора файлов/поля, проверки и обновления | NOT_RUN |
| Критерии/defaults, progress, журнал, ошибка/отмена, сохранение путей | NOT_RUN |
| Новый шаблон и DEMO outputs открываются без repair/links prompts | NOT_RUN |
| Шесть листов, четыре таблицы, диаграмма, оформление/печать по образцу ГДИС | NOT_RUN |
| Формулы/пересчёт/границы ёмкости, отсутствие #REF!/#VALUE!/#N/A | NOT_RUN |
| UNKNOWN сохранён; баллы, ранги и TOP пусты; №7–9 не названы доказанными | NOT_RUN |
| Условные даты отличаются от точных; интервалы cutoff видны | NOT_RUN |
| Save/close/reopen без repair и потери связей/форматирования | NOT_RUN |

Для ограниченной native automation можно использовать
`scripts/native_excel_acceptance.ps1` с DEMO input и новым локальным output dir.
Он требует установленного Excel COM; успех automation оставляет manual_status
NOT_RUN. openpyxl и tests/formula_subset.py не являются Excel engine.

Окончательный принятый образец ГДИС и методика подтверждаются отдельно. Реальные
входы, row-level diagnostics, screenshots, ответы и private acceptance receipts
не публикуются; замечания передаются владельцу по согласованному приватному каналу.
