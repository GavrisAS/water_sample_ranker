# Water Sample Ranker

**TECHNICAL PREVIEW — НЕ УТВЕРЖДЁННЫЙ РЕЙТИНГ**

Python 3.14; Windows x64 приложение для ознакомления с локальными источниками,
признаками и диагностикой отбора проб воды. Баллы, ранги и TOP остаются пустыми
до утверждения методики. UNKNOWN не заменяется FALSE/нулём. №7–9 — наблюдения
и кандидаты. Данные не отправляются в GitHub.

Пользователь Windows распаковывает ZIP целиком и запускает
`Water_Sample_Ranker.exe` вместе с `_internal/` и `templates/`; Python/uv не нужны.
Инструкция: [первый запуск](docs/README_ПЕРВЫЙ_ЗАПУСК.txt).
Книга сохраняет шесть листов README/Настройки/Данные/Критерии/Расчет/Рейтинг.

Для разработки: `uv sync --locked`, затем `uv run water-sample-ranker-gui`.
CLI использует тот же service: `uv run water-sample-ranker --help`;
`validate`, `update --field …`, `update-all --output-dir …`.
Пути задаются в GUI, параметрах CLI или локальном `config.toml`
по [безопасному примеру](config.example.toml). Источники и шаблоны не изменяются;
для результатов всегда нужны новые имена. Для пакетной операции COMPLETE
требует проверки маркера, привязки к источникам/дате/версии и всех SHA-256;
одно имя `bundle-complete.json` не доказывает завершённость.

Полностью синтетическая демонстрация:
`uv run python -m scripts.make_synthetic_demo --output-dir artifacts/private/demo-new`.
Не размещайте реальные книги и приватные отчёты в Issues. Вопросы заказчику
ведутся отдельно и не публикуются.

Проверки: `uv sync --locked`, `uv run ruff check .`,
`uv run ruff format --check .`, `uv run ty check`, `uv run pytest`.
Публичные проверки обходятся без производственных файлов.

Команды разработчика по образцу ГДИС собраны в [Makefile](Makefile):
`make sync`, `make check`, `make run`, `make cli`, `make version`, `make demo`,
`make build` / `make build-windows`. `make help` перечисляет параметры.
`make privacy`, `make public-check` и `make audit` выполняются из очищенной
публичной копии; исходная локальная история с приватными документами их не проходит.
`make clean` удаляет только кэши проверок.

[Релизный процесс](docs/RELEASE_PROCESS.md) использует серию `v0.1.PATCH`,
начиная с `v0.1.1`; версия исходников согласована как `0.1.1` вместо прежней
developer-метки `0.2.0`. При сборке release metadata меняется только в isolated
snapshot. Идентификатор Excel-формата `water-v0.2-stage-A` и подписи v0.2 —
историческая схема шаблона, а не версия приложения или утверждённой методики.

Автоматические Windows/Qt/OOXML проверки не подтверждают Microsoft Excel
или человеческую приёмку Windows UX. Эти human gates остаются NOT_RUN/PENDING
до отдельной подписанной проверки: [приёмка](docs/WINDOWS_EXCEL_ACCEPTANCE.md).
[Аудит из четырёх файлов](docs/AUDIT_HANDOFF.md) выполняется отдельно от release;
handoff никогда автоматически не прикладывается к публичному релизу.
