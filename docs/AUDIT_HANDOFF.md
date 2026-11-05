# Безопасный audit / review / handoff

Stage B.1: handoff остаётся отдельным четырёхфайловым локальным комплектом и не
прикладывается автоматически к публичному Release. После проверенного релиза
сборщик может включить `review/reports/public_release.json` из локальной строгой
квитанции: source/tag/run URLs, completed jobs и SHA скачанных assets. Автономный
verifier проверяет привязку к HEAD, имена и hashes, но не обращается к GitHub.
Это STATIC_DOWNLOADED_EVIDENCE_ONLY; native Excel и human Windows UX NOT_RUN.
Анкета и её содержимое не входят в handoff. Команду запускают из реального
проекта; приватные исходные документы заменяются нейтральными проекциями только
в архиве, а их diff исключается. Оригиналы остаются неизменными.

Этот инструмент передаёт текущее дерево и доказательства для независимого ревью.
Корректирующий этап A.1 описан в STAGE_A1_REPORT.md; методика не меняется.
Старые acceptance/qualification — исторические заявления, пока свежая команда не
подтвердила конкретный gate. Численность по месторождениям не зашита в сборщик.

## Единственный конечный каталог

`<PROJECT_ROOT>/artifacts/handoff/` — ровно четыре обычных файла нового ID
непосредственно в каталоге, без вложенного каталога ID или stage. Старые комплекты
и остатки аварий сохраняются. stdout печатает четыре абсолютных пути, bytes и SHA-256.
Корень подтверждается pyproject.toml, scripts/build_handoff.py и Git top-level;
вложенный checkout не может стать конечным root. CWD не влияет на результат.
`--output-dir` совместим только с этим же физическим каталогом; другие пути,
`..`, symlink traversal и коллизии case/Unicode блокируются до записи результата.

Если рабочая история приватна, а релиз опубликован из отдельной публичной копии,
добавьте `--public-source-root <PUBLIC_CHECKOUT>`. Её чистое Git-дерево проверяется
по allowlist и побайтно сверяется с разрешённой проекцией реального root.
Commit/checks/frozen evidence относятся к этой проекции, production и preservation —
к реальному root; конечный каталог всегда остаётся в реальном root.
Приватные файлы в публичную копию не переносятся.

`make audit` вызывает тот же builder; дополнительные флаги задаются через
`make audit AUDIT_ARGS='--sync-locked --with-production-validation'`.
`make privacy` и `make public-check` по-прежнему требуют чистой публичной истории.

## Четыре файла для ChatGPT

| Файл | Назначение |
|---|---|
| water-sample-ranker-audit-ID.tar.gz | Allowlisted исходники, тесты, lockfile, безопасная конфигурация, отобранные документы и filtered patches текущего дерева |
| water-sample-ranker-review-ID.tar.xz | REPORT, карта кода с выдержками правил, stdout/exit code/duration, агрегаты, структурный Excel audit, standalone verifier |
| water-sample-ranker-handoff-ID.json | Сквозной ID, UTC, fingerprint, Git состояние, роли входов, gates, индекс evidence и SHA/bytes архивов |
| water-sample-ranker-handoff-verification-ID.json | Квитанция другого процесса: хеши, полный member inventory, policy scan и fresh extraction |

Загружайте **все четыре файла одного ID**. Папки с входами, локальное окружение,
приватные логи и целый ZIP проекта передавать не нужно. Ничего не публикуется на
GitHub и не отправляется в сеть. Внешние упаковщики tar/xz не нужны.

## Linux

Из корня проекта, после установки Python 3.14 и uv:

```bash
uv --version
uv sync --locked --offline
uv run python scripts/build_handoff.py --mode share-safe
uv run python scripts/verify_handoff.py artifacts/handoff/water-sample-ranker-handoff-ID.json
```

Подставьте ID из stdout сборщика. Если зависимостей ещё нет в локальном cache,
отдельно выполните разрешённую вами обычную установку `uv sync --locked`.
Сам сборщик использует offline subprocess и не разрешает скачивания.

Из другого текущего каталога:

```bash
uv run --project "<PROJECT>" python "<PROJECT>/scripts/build_handoff.py" --mode share-safe
python "<PROJECT>/scripts/verify_handoff.py" "<PROJECT>/artifacts/handoff/water-sample-ranker-handoff-ID.json"
```

Обозначения в угловых скобках заменяются локальными путями. Внутренние команды
всегда получают корень проекта как cwd. Выход по умолчанию — artifacts/handoff
относительно корня, а не относительно текущего каталога вызова.

## Windows PowerShell

```powershell
uv --version
uv sync --locked --offline
uv run python scripts/build_handoff.py --mode share-safe
Get-ChildItem artifacts/handoff
uv run python scripts/verify_handoff.py artifacts/handoff/water-sample-ranker-handoff-ID.json
Get-FileHash artifacts/handoff/water-sample-ranker-audit-ID.tar.gz -Algorithm SHA256
```

Используются pathlib, stdlib gzip/lzma/tarfile/zipfile и subprocess. Нет bash,
fcntl, Docker, демонов или настроек Git. Относительные archive paths POSIX;
drive/UNC, reserved names, controls, NFKC/casefold collisions и traversal запрещены.
Длина member path ограничена 240 символами, компонента — 120. Более длинный
недопустимый путь блокирует сборку, вместо неявной нормализации.

## Что выполняется по умолчанию

Ruff lint, Ruff format check, ty, полный pytest, CLI validate/update --help,
headless Qt window без production config; версии Python/uv/ruff/ty/pytest.
`uv sync` выполняется только с `--sync-locked` и только offline.
Проверки имеют timeout (по умолчанию 300 секунд; --timeout задаёт 0…3600).
Нулевой exit code с неполным/усечённым логом не считается PASS.
`--skip-checks` явно помечает инженерные команды NOT_RUN; версии всё равно
проверяются. Код возврата сборщика 0 означает готовность проверенного комплекта,
а не PASS всех продуктовых gates: итоговый статус напечатан отдельно в stdout/JSON.
Критичная ошибка безопасности или упаковки — код 2 и безопасная категория в stdout,
без секретного значения и без дополнительного файла в конечной папке.

Приватные книги по умолчанию только хешируются для контроля неизменности.
Их ячейки/схемы и строки не читаются для интеграционной проверки без флага.
Присутствие роли определяется по конфигурации; если она не настроена, роль UNKNOWN,
а не угаданный источник. Старый qualification допускается как историческая
проекция: возраст/хеш/агрегированные количества, fresh_execution=false.

## Явный opt-in production / существующий frozen smoke

```bash
uv run python scripts/build_handoff.py --mode share-safe --sync-locked --with-production-validation --with-frozen-smoke
```

Production worker запускается в отдельном subprocess с тем же timeout. Он
использует текущий shared service, фиксирует дату один раз, делает validate и
update каждого найденного поля. Книги и полные JSON/log создаются только в
отдельном системном TemporaryDirectory, вне входов и передаваемого каталога,
и удаляются после агрегирования. Сбой не раскрывает traceback/номера/пути.

В review сохраняются: категории фонда, сверка сумм, количество строк,
MATCHED/AMBIGUOUS/UNMATCHED, стадии событий, двойной счёт, TRUE/FALSE/UNKNOWN/N/A
по девяти критериям, покрытие, конфликты пар, последняя дата, cache/parse counts,
фактически записанные строки, SHA outputs и результаты повторного stdlib-чтения
состояний/количеств/пустых score-rank formulas. Raw workbook rows исключены.

`--with-frozen-smoke` повторяет `--smoke-test` существующей сборки; саму сборку
не запускает. PASS привязки требует свежую квитанцию, совпадающие source commit,
code/resource SHA и build/smoke logs; старый бинарник не получает новый PASS.
Windows/native Excel и ручной визуальный UX на этом хосте — NOT_RUN/PENDING.

## Политика исходников и шаблона

Разрешены выбранные файлы корня, Python из src/tests/scripts, PowerShell scripts,
workflow YAML и перечисленные в verifier документы. Не используются git archive
или весь рабочий каталог: безопасные staged/unstaged/untracked bytes берутся с диска.
Porcelain details и staged/unstaged/untracked поля относятся к allowlist;
полные количества без раскрытия исключённых имён — total_state_counts,
хеш исходного porcelain — full_porcelain_sha256.
Git HEAD может отсутствовать; это SOURCE_IDENTITY_PARTIAL с head=null. Porcelain и
patches фильтруются по allowlist; неизвестные/приватные пути не раскрываются.

В двух исходных заголовках и строке авторства docs/ATTRIBUTION.md персональная
атрибуция заменяется в **копии архива**; указание исходного проекта сохраняется.
Оригиналы не меняются. SOURCE_MANIFEST хранит original/archived SHA/bytes и список
transformations. Это SHARE_SAFE_WORKTREE_PROJECTION, а не ложное обещание полностью
побайтной копии исходника с персональными данными. Отдельная полная постановка,
персональная атрибуция, старые source audit JSON и production примеры не копируются;
их роли/безопасные fingerprints могут присутствовать без содержимого/имён.

Бинарный XLSX по умолчанию исключён: TEMPLATE_BINARY_EXCLUDED/PARTIAL.
Обязательный JSON содержит структуру, листы, таблицы/диапазоны, ёмкость, формулы,
диаграммы/защиту и structural gate без значений ячеек. При необходимости:

```bash
uv run python scripts/build_handoff.py --mode share-safe --include-template-binary
```

Флаг не является обходом проверки. Допускается только пустой calculator template,
который проходит scan всех XML/metadata, отсутствие data rows и literal calculation
rows, комментариев, непроверенных shared strings, внешних links/formulas,
VBA/payloads, ZIP extra metadata. Неуверенность/находка блокирует включение, исходник
не редактируется. Существующая синтетическая XLSX с множеством похожих на реальные
номеров по умолчанию тоже исключена; Python synthetic fixtures передаются.

Даты, пороги и веса не затираются. Исключение по правилу №23 и синтетические
идентификаторы в исходном тестовом коде допустимы как код/постановка, не как
production rows. JSON raw identity/source-location/raw-value поля запрещены.
Content scan охватывает source text, filtered patches, JSON/MD/logs и optional XLSX.
Это систематическая policy-проверка с заявленной областью, не универсальный
семантический детектор всех форм персональной информации.

## Независимая проверка только четырёх файлов

Полный stdlib verifier лежит внутри обоих архивов. Для нового reviewer-каталога
с четырьмя файлами можно безопасно извлечь только фиксированный member
`review/verify_handoff.py` (не применять extractall до проверки):

```python
from pathlib import Path
import hashlib
import json
import tarfile

candidates = [
    p
    for p in Path(".").glob("water-sample-ranker-handoff-*.json")
    if "-verification-" not in p.name
]
assert len(candidates) == 1
manifest_path = candidates[0]
manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
entry = manifest["artifacts"]["review"]
archive_path = Path(entry["path"])
assert archive_path.name == entry["path"]
assert archive_path.stat().st_size == entry["bytes"]
assert hashlib.sha256(archive_path.read_bytes()).hexdigest() == entry["sha256"]
with tarfile.open(archive_path) as archive:
    item = archive.getmember("review/verify_handoff.py")
    assert item.isfile() and not item.linkname and item.size < 100_000
    stream = archive.extractfile(item)
    code = stream.read(100_001)
    assert len(code) == item.size
with Path("verify_handoff.py").open("xb") as target:
    target.write(code)
```

Выполните snippet обычным Python (в PowerShell допустим here-string, в Linux —
`python -c`/сохранённый локальный файл). Затем:

```bash
python -I -B verify_handoff.py water-sample-ranker-handoff-ID.json
```

Верификатор ничего не загружает из сети, не импортирует приложение, не читает
repo/config/data. Он ограниченно распаковывает каждый regular member в новый
tempfile, сверяет bytes/SHA, manifests, source fingerprint, gates/log bindings,
полноту и политику. Отсутствующая квитанция при обычной проверке — FAIL.
`--create-receipt` предназначен для первого запуска builder; существующая
квитанция не заменяется. Builder делает independent trial в staging, публикует
архивы и receipt, затем manifest как commit marker, и ещё раз запускает новый
процесс из другого временного cwd по опубликованным байтам. При сбое удаляет
только собственные file identities, старые успешные комплекты и подменённые
чужие файлы сохраняет. Staging находится временно рядом с конечной папкой и
очищается при штатном завершении/исключении. Hard links не заменяют существующие
имена. Четыре имени не являются единой транзакцией файловой системы: внезапное
завершение процесса/питания может оставить незавершённые файлы. Такой набор не
считается опубликованным без последнего manifest и успешного verifier; остатки
не удаляются автоматически и блокируют повторное использование того же ID.

Хеши не заменяют цифровую подпись. Согласованная подмена всех файлов и verifier
не может быть обнаружена без доверенного внешнего канала. PASS квитанции относится
к целостности и safety policy, business_correctness всегда UNKNOWN.

Самостоятельно посмотреть названия members без распаковки:

```python
import tarfile

with tarfile.open("water-sample-ranker-audit-ID.tar.gz") as archive:
    for item in archive:
        print(item.name)
```

Убедитесь, что нет data/private/env/cache/reference workbook. SHA всех четырёх
файлов напечатаны сборщиком; два archive SHA также в sidecar, sidecar SHA — в
receipt. JSON канонические и имеют integrity digest тела без собственного digest;
внутренний member manifest исключает только себя. Циклических hashes нет.

## Статусы и обязательные evidence

PASS — выполненная проверка с полным логом/результатом. FAIL — проверка выявила
ошибку; BLOCKED — безопасность/упаковка запрещает передачу. PARTIAL — комплект
есть, но область проверки ограничена. NOT_RUN — команда не выполнялась, опция не
дана, платформа недоступна. UNKNOWN — утверждение не доказано. PENDING используется
в тексте для человеческих gates; в gates.json для них NOT_RUN.

| Evidence | Путь в review | Как трактовать текущий запуск |
|---|---|---|
| Обзор и ограничения | REPORT.md | Verdict по фактическим gates; claims docs отдельно |
| Команды/версии/логи | reports/gates.json, logs/ | Точные argv/cwd/duration/returncode/completeness |
| Фонд/matching/критерии | reports/coverage.json | Свежий PASS только с production opt-in; иначе NOT_RUN |
| Предметные границы | reports/DOMAIN_AUDIT.md | Synthetic pytest, без изменения методики |
| Роли и fingerprints | reports/source_inventory.json | Без реальных имён и абсолютных путей |
| Excel | reports/excel_structure.json | Structural отдельно от NOT_RUN native/visual |
| Privacy/preservation | reports/privacy.json, preservation.json | Обязательный PASS перед передачей |
| Исходники/изменения | audit/SOURCE_MANIFEST.json, evidence/*.patch | Фактическое дерево и объявленные проекции |
| Независимая квитанция | внешний verification JSON | PASS по физическим архивным байтам, не по бизнес-приёмке |

Запуски разных ID могут иметь разные hashes из-за UTC, длительностей и Git dirty
state. Побайтная детерминированность не заявляется. Число members <=2048,
member <=8 MiB, общий распакованный объём <=64 MiB, архив <=32 MiB. Превышение
BLOCKED, split parts не поддерживаются. При ошибке не отправляйте комплект:
проверьте безопасную категорию отказа в stdout, устраните причину и
создайте новый ID. Старые файлы не ротируются и не удаляются автоматически.


## Дополнения Stage B

Текущие baseline/after: STAGE_B_BASELINE.md / STAGE_B_REPORT.md. В allowlist
включены эти документы, CUSTOMER_DECISIONS_STAGE_B.md, OPTIONAL_INPUTS.md,
WINDOWS_EXCEL_ACCEPTANCE.md и EXCEL_TEMPLATE_AUDIT.md (ссылка ATTRIBUTION теперь
доступна также в извлечённом review). Исторические 164/211/267 помечены историей.

Production worker использует run_update_all: **один validate/фиксированная дата**,
отдельные XLSX по всем полям, затем независимое чтение количества строк/состояний/
пустых score/rank. coverage.json дополнен агрегатами №7–9 (пары, изменения,
типы/cutoff/stage timeline, кустовые кандидаты и quality reasons). Номера,
координаты и сырые промысловые значения в эту проекцию не входят. Полные private
register/JSON/log/output/synthetic XLSX остаются исключены.

`--include-template-binary` согласует archive member, manifest/privacy flag,
template_binary gate и excel_structure.json binary=SCANNED_BLANK_TEMPLATE_INCLUDED.
Независимый verifier отклоняет любое несоответствие, включая исторический дефект
TEMPLATE_BINARY_EXCLUDED при фактическом включении. Пустой template v1 проходит
строгий content scan; writer создаёт совместимую evidence v2 копию A:BA.

Свежая сборка: `uv run python scripts/build_frozen.py`. Она копирует только код и
ресурсы в новый isolated source, строит новый onedir, запускает smoke и сохраняет
private receipt/pointer. При --with-frozen-smoke сборщик проверяет code/resource
SHA, commit, binary/template и build/smoke logs; затем заново запускает бинарник.
Только совпадение даёт VERIFIED_CODE_RESOURCE_SNAPSHOT. Нет свежей привязки —
прежний source_binding UNKNOWN/PARTIAL, без переноса старого PASS.
Стандартные очищенные build/smoke logs входят в review, бинарник — нет.
Независимый verifier связывает исходные SHA build provenance с audit manifest.
Это воспроизводимый локальный audit, не аттестация цепочки поставки.

Полный запуск после fresh gates/build:

```bash
uv run python scripts/build_handoff.py --mode share-safe --sync-locked --with-production-validation --with-frozen-smoke --include-template-binary
```

Каждый новый ID содержит свои команды/логи. Native Excel/visual остаются NOT_RUN без отдельной фактической приёмки;
Windows automation оценивается по release receipt. План ручной проверки —
WINDOWS_EXCEL_ACCEPTANCE.md. Handoff остаётся локальным и не загружается в сеть.
