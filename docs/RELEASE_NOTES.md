# Stage B.1 — technical preview

Первый push зарезервировал v0.1.1; его Windows gates выявили locale/newline и
переносимость путей/тестов. Номер остаётся пропущенным, не передаётся другому
commit и не выдаётся за опубликованный релиз. Следующий eligible push — v0.1.2.
Исправлены явное UTF-8 чтение, канонические LF metadata/checksums, ASCII protocol
автономного verifier и наблюдаемое изменение mtime на NTFS. Добавлен Makefile
по образцу исходного проекта; `version` не увеличивает release counter.

v0.1.2 прошёл Linux/Windows checks, fresh Windows build и frozen GUI/version,
но остановлен на frozen CLI validate (exit 2). Этот номер также не переиспользуется.
Следующий кандидат v0.1.3 добавляет явный приватный --cli-log для windowed EXE
и проверку фактического вывода CLI --version. Подробные smoke logs не включаются
в release assets; в Actions допустима только обезличенная синтетическая диагностика.

Windows candidate с --cli-log прошёл frozen GUI/CLI. Privacy scan выявил маркеры
PEM-парсера в трёх стандартных Qt DLL. Они независимо сверены с публичным
PySide6 wheel из lockfile; исключение ограничено точными reviewed SHA-256.
Неизвестные бинарники с такими маркерами по-прежнему блокируются.
В onedir добавлены upstream license notices/texts; лицензия приложения не меняется.

TECHNICAL PREVIEW — НЕ УТВЕРЖДЁННЫЙ РЕЙТИНГ.

Исправлены две технические ошибки: частичные даты ГТМ сравниваются с cutoff по
полному календарному интервалу, а bundle-complete публикуется атомарно и требует
проверки файлов/привязки/SHA. Историческая идентификация этапов и критерии №1–6
сохраняются. №7–9 остаются UNKNOWN; баллы, ранги и TOP не вычисляются.

Внедрена трёхкомпонентная серия v0.1.PATCH, source-bound Windows onedir,
сканирование всей публичной истории и архивов, immutable tag, проверка draft и
повторное скачивание assets. При rerun существующий релиз проверяется без замены.
Source/development версия согласована на 0.1.1; Excel format ID v0.2 отделён от неё.

Фактические результаты конкретного релиза находятся в его BUILD_EVIDENCE.json,
checksums и GitHub Actions run. Production qualification в public CI NOT_RUN;
native Excel и человеческая Windows UX NOT_RUN/PENDING. Вопросы заказчику ведутся
отдельно и не публикуются. Подробности — RELEASE_PROCESS.md.
