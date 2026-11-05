# Архитектура

`CLI/Qt → shared service → readers → typed models → lifecycle/current plast →
whole-sample selection → pure criteria → coverage → OOXML copy writer`.

Общие типы: immutable dataclasses, Evidence/SourceLocation, четыре Truth state,
nullable normalized_score/weighted_contribution. Source readers не содержат GUI;
criteria не обращаются к Excel; scoring считает только evidence coverage.

validate читает и возвращает отчёт, не создаёт outputs. update требует field,
повторяет validate, проверяет output, формирует временную книгу в каталоге
результата, проверяет структуру и неизменность inputs, публикует через atomic
no-replace hard link и создаёт JSON/log exclusively. При ошибке удаляет только
свои новые выходные файлы; существующие результаты не трогает.

Одноразовая адаптация root workbook выполняется scripts/derive_template.py.
Runtime writer патчит sheet2 (дата/поле/TOP-N/пороги), sheet3 и tblData. Формулы,
стили и chart bytes нового шаблона сохраняются. Шаблон имеет capacity 1000,
превышение блокируется. Нет COM/VBA/Excel engine и внешних runtime links.

QThread worker передаёт только result/error/progress signals. GUI использует
immutable snapshot настроек на запуск; edit inputs сбрасывает valid state.
Численные пользовательские пороги явно PENDING; математический скоринг запрещён.

Кодовый reference gdis_loader не импортируется в runtime. Происхождение адаптаций
и отличия указаны в [ATTRIBUTION.md](ATTRIBUTION.md); сам reference остался неизменным.


Stage B: один диагностический слой diagnostics.py и domain/evidence.py;
Truth/score API и №1–6 не меняются. Typed ГСС, history pad, optional JSON adapters
изолированы от lifecycle rules. run_update_all делает один validate с фиксированной
датой и fingerprints, использует тот же _write_validated, откатывает свои файлы
при исключении и последним создаёт no-clobber bundle-complete.json. До marker
набор не завершён. Наличие marker не доказывает COMPLETE: проверяются JSON,
имена/число файлов, SHA XLSX/JSON/log и source/date/version. Marker публикуется
через отдельный fsynced temporary и atomic exclusive hard link; чужие file
identities и crash leftovers сохраняются. Одновременная видимость всех
файлов и откат после kill/power loss не обещаются. Qt отмена — на checkpoints.
Входы повторно проверяются до публикации и перед marker всего bundle.
