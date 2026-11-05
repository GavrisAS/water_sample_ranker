# Необязательные источники Stage B — предлагаемый контракт v1

Это обменная схема прототипа, **не утверждённый производственный формат**.
Подключение только явное: `[paths] gtm_plan = "…json"`, `geodata = "…json"`
или CLI `--gtm-plan` / `--geodata`; discovery не угадывает эти файлы.
В GUI оба поля помечены необязательными. Пустой путь/нет файла:
SOURCE_UNAVAILABLE, критерий UNKNOWN. Неверная схема: SCHEMA_DRIFT, весь источник
отклоняется с понятным сообщением; основные источники продолжают проверяться.

UTF-8 JSON, размер до 4 MiB; ровно schema/records в корне, строгие ключи в каждой
строке, без дубликатов JSON-ключей. Identity — canonical field, display well,
stage sequence (целое >=0), canonical plast. Aliases и пласты не угадываются.
JSON provenance — имя файла и `/records/N` (нулевой индекс). Непривязанные
записи не повышают покрытие и не превращаются в отрицательный ответ.

Строго синтетический план:

```json
{"schema":"water-gtm-plan-v1","records":[{"field":"береговое","well":"DEMO-ALFA","stage":0,"plast":"ПК1","date":"2026-12-01","precision":"day","label":"DEMO-PLAN"}]}
```

date: YYYY-MM-DD / MM.YYYY / YYYY; precision: day/month/year, точно соответствует
строке. Source precision сохраняется; effective-date policy первого дня
показывается отдельно. PLAN_ONLY не является HISTORY_FACT и не доказывает
проведение. Будущая дата помечается AFTER_CUTOFF. Окно и типы не утверждены.

Строго синтетические координаты (не карта месторождения):

```json
{"schema":"water-geodata-v1","records":[{"field":"береговое","well":"DEMO-ALFA","stage":0,"plast":"ПК1","x":0.1,"y":0.2,"crs":"EPSG:4326","coordinate_source":"SYNTHETIC_NO_FIELD_MAP","version":"DEMO-v1"}]}
```

v1 поддерживает только явно заданный EPSG:4326: x=longitude [-180,180],
y=latitude [-90,90], конечные числа; bool/NaN/Infinity запрещены. Другой CRS
отклоняется, преобразования не выполняются. Дубли identity отклоняются целиком.
coordinate_source/version обязательны. Привязка к текущему этапу/пласту точная.
Расстояния/радиусы не вычисляются: даже корректные координаты не утверждают
правило №9. Текущая витрина использует только числовой куст в единственной
подтверждённой строке истории; пустые/непонятные кусты не дополняются.

Воспроизводимый генератор создаёт оба JSON, синтетический мониторинг,
фонд/историю/ЛХА и три книги по две DEMO-скважины:

```bash
uv run python scripts/make_synthetic_demo.py --output-dir artifacts/private/synthetic-stage-b
```

Нужен новый каталог. Генератор не читает production data и использует только
пустой шаблон. Эти книги допустимы для локальной демонстрации; share-safe handoff
передаёт генератор/fixtures, а бинарную книгу с заполненными строками исключает.
