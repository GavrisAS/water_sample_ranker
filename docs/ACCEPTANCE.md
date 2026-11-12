# Приёмка технического прототипа

TECHNICAL PREVIEW — НЕ УТВЕРЖДЁННЫЙ РЕЙТИНГ.
Методика предварительного балла PROVISIONAL. Native Microsoft Excel и человеческая Windows
UX: NOT_RUN/PENDING. Автоматический PASS относится только к проверенной области.

Обязательны Python 3.14, `uv sync --locked`, Ruff check/format, ty, pytest,
CLI и Qt headless, synthetic validate/update/update-all, независимые OOXML/formula
goldens, immutable sources, public tree/history/archive/template scans.
Команды объединены в `uv run python -m scripts.run_checks --public` для чистой
публичной копии. Полные локальные производственные источники для CI не требуются.

P2 regression проверяет календарные границы/precision/cutoff фактов и планов,
UNKNOWN №8, отсутствие изменений lifecycle, atomic/no-clobber marker, fault
injection до/во время/после записи, реальные конкурирующие процессы, checksum,
binding и безопасное сохранение чужих объектов/остатков аварии.

Релиз требует завершённых Linux checks и Windows checks/build, fresh x64 onedir,
frozen GUI/version/package metadata/template и CLI smoke с DEMO, проверку всего
ZIP, точный SHA source/tag, checksums всех assets и повторное скачивание prerelease.
Точные counts/statuses находятся в evidence конкретного run; YAML и Linux build
не подтверждают выполнение Windows job. Релиз всегда prerelease до human gates.

Критерий №1 предварительный; №6 — gate без ложного бонуса, №7 — подтверждённый
диаметр, №8 — ручной ГТМ, №9 отложен. Неизвестный вклад остаётся недоступным;
подтверждённая сумма без перенормировки и предварительные места доступны.
Покрытие evidence — отдельная величина и не рейтинг.

Производственная регрессия выполняется локально с неизменными файлами и
построчным сравнением; private receipts не публикуются. Синтетические данные
не доказывают корректность конкретного производственного пакета.

Человеческий checklist — [WINDOWS_EXCEL_ACCEPTANCE.md](WINDOWS_EXCEL_ACCEPTANCE.md).
Исторические результаты другого продукта не переносятся. Публичные правила
версий/приватности — [RELEASE_PROCESS.md](RELEASE_PROCESS.md); отдельный audit/handoff
содержит четыре файла одного ID и проверяется автономным verifier.
