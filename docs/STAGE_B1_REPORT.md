# Stage B.1 — область автоматической проверки

Публичный технический прототип. Итог публикации подтверждается только фактическим
GitHub Release, завершённым Actions run, скачанными assets и SHA-256.
Этот документ не подменяет квитанцию конкретного commit/tag.

P2-01: отдельный CalendarInterval сохраняет исходную точность. DAY — точка,
MONTH/YEAR — реальные включительные календарные границы; пересечение cutoff
даёт STRADDLES_CUTOFF, отсутствие — UNKNOWN_DATE. Effective date явно условна.
Lifecycle first-day policy не менялась; №8 не становится TRUE из даты.

P2-02: temporary JSON в том же каталоге, flush/fsync, atomic no-clobber hard link.
COMPLETE проверяет schema, имена/количество выходов, SHA XLSX/JSON/log,
source/date/version binding и обязательные отчёты. Ошибки откатывают собственные
file identities; чужие файлы и crash leftovers не очищаются автоматически.

Тесты включают граничные/конфликтные даты фактов и планов, приватную сериализацию
и агрегат, fault injection записи marker, конкурирующие процессы, повреждённые
файлы, версионные резервации/rerun/collision, leak fixtures в temporary каталогах,
archive/OOXML scans и сохранение пустых score/rank/TOP. Производственные построчные
регрессии ведутся локально и не включены в public evidence.

Публичный root не наследует локальную историю/производственные документы.
Релизная инструкция и source/release metadata описаны в RELEASE_PROCESS.md.
PRODUCT_ACCEPTANCE_PARTIAL: методика PENDING, native Excel и ручная Windows UX
NOT_RUN. Анкеты и private discrepancy registers ведутся отдельно.
