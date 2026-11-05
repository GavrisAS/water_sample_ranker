# Stage B.1 — technical preview

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
