# Публичный prerelease и версии

TECHNICAL PREVIEW — НЕ УТВЕРЖДЁННЫЙ РЕЙТИНГ. Методика PENDING;
native Excel и ручная Windows UX NOT_RUN. Синтетическая проверка не является
квалификацией производственных источников. Release evidence содержит только
автоматические доказательства, без приватного handoff или вопросов заказчику.

## Резервирование номера

Выделенный неизменяемый `.github/workflows/release-main.yml` запускается только
на `push main`. Его `github.run_number` определяет `v0.1.PATCH`: первый run —
`v0.1.1`, десятый — `v0.1.10`. Workflow внедряется в новый репозиторий без истории
Actions; bootstrap требует проверки отсутствия прежних запусков. Файл workflow
нельзя переименовывать/удалять или дополнять другими triggers. Для другой серии
нужна отдельная явно проверенная миграция, а не вычисление по свободным тегам.

Каждый eligible push резервирует номер, даже если checks/build/publication упали.
Пропуски допустимы; номер не передаётся другому commit. `run_attempt` используется
только для выбора immutable Actions artifact. Rerun сохраняет версию и run ID.
PR, tag push, локальная работа, Checks dispatch и Windows candidate dispatch
не резервируют номера и не создают Release.

Автоматической отмены main release runs нет. Concurrency действует отдельно для
каждого тега (`cancel-in-progress: false`), поэтому разные зарезервированные
версии не вытесняют pending jobs друг друга. FIFO не требуется: номера заранее
привязаны к runs, tags независимы, а publish никогда не назначает следующий номер.
`--latest=false` исключает переопределение latest при завершении не по порядку.

## Единственная версия артефакта

Source/development версия согласована как `0.1.1` вместо исторической `0.2.0`.
`scripts/build_frozen.py --version … --source-sha …` требует чистый HEAD и копирует
только `build_inputs` в новый isolated snapshot. Там генерируется `_build_version.py`
с версией и commit, изменяются копии pyproject/uv.lock и создаётся `.dist-info/METADATA`.
Исходный pyproject/lock не меняются для каждого CI run. Runtime `__version__`, CLI,
GUI «О программе», importlib.metadata, build manifest и имена ZIP совпадают с тегом.
Manifest различает fingerprint исходных и преобразованных build inputs.

Исторический `water-v0.2-stage-A` и подписи v0.2 в пустом Excel-шаблоне —
идентификатор проектной схемы/методики, не версия приложения или признак утверждения.
Этот проверенный шаблон сохранён побайтно. Версия приложения явно указана отдельно.

## Gates и права

Checks выполняются на Linux/Windows с Python 3.14, `uv sync --locked`, Ruff,
format, ty, pytest, CLI, Qt offscreen, synthetic validate/update/update-all,
OOXML/formula/score guards, protected-input checks и public privacy gate.
Производственных файлов на runners нет и они не нужны тестам.

Release run проверяет точный source SHA; Linux gates предшествуют Windows build.
PowerShell использует fresh PyInstaller x64 onedir; выполняются frozen GUI/template/
metadata smoke и реальный frozen CLI validate/update/update-all с DEMO-файлами.
Проверяется весь onedir, обязательные ресурсы, PE x64, inventory SHA каждого файла,
вложенный base_library.zip, пустой шаблон, config и русская инструкция.
Только четыре проверенных release assets передаются в Actions artifact.
Manual candidate также сканируется до upload, но Release не создаёт.

Все jobs имеют `contents: read`; только release job — `contents: write` и
`actions: read` для проверки фактических jobs/artifacts. Нет pull_request_target.
Официальные checkout/setup-uv/upload-artifact закреплены SHA, проверенными по
upstream tags. Сборочные логи остаются локальными для job; публикуются только
явно сформированные агрегаты. Никакие токены не записываются в evidence.

## Публикация и повторный запуск

`scripts/publish_release.py` сверяет GitHub run/source/workflow и реальные
Linux/Windows job conclusions, скачивает candidate, повторно проверяет точные
имена четырёх assets, checksums, source fingerprint, runtime smoke и manifests.
Лишь после gates создаётся lightweight tag строго на проверенный commit.
Force/move/delete отсутствуют. Существующий чужой tag — CONFLICT_FAIL.

Release сначала создаётся draft prerelease. Полнота всех загруженных файлов
проверяется повторным скачиванием; затем draft открывается публично и ещё раз
проверяется как `isPrerelease=true`. Неполный draft не объявляется опубликованным.
Rerun при существующем Release сначала скачивает его и проверяет tag/commit/run ID,
manifests и SHA всех assets; совпадение даёт ALREADY_PUBLISHED_VERIFIED.
Несовпадение — CONFLICT_FAIL без перезаписи assets. Полный draft того же run можно
допубликовать; неполный требует расследования, не тихого overwrite.

## Public privacy preflight

Первичная публикация использует отдельный новый root commit из
`scripts.prepare_public`; прежняя локальная история остаётся у владельца и не push-ится.
`scripts/public_files.txt` — явный список разрешённых путей. Приватные исторические
документы заменяются в копии нейтральными заглушками без содержания вопросов/источников.
Исходные документы не удаляются. Права на код и производный шаблон подтверждены
владельцем; новая лицензия для стороннего переиспользования не назначена.

`scripts.public_gate` проверяет exact tracked tree, все исторические trees/blobs,
`rev-list --objects --all`, размеры, типы, имена и содержимое. Затем проверяется
git archive. Пустой XLSX проверяется как OOXML/ZIP с фиксированным reviewed SHA:
листы/hidden states, data rows, names, external links, metadata, sharedStrings,
comments и payloads. Отрицательные тесты создают утечки только в TemporaryDirectory.
Это проверка заявленной области, а не универсальный семантический детектор данных.
Новые файлы/шаблоны требуют явного review allowlist и тестов.

```bash
uv sync --locked
uv run python -m scripts.run_checks --public
uv run python -m scripts.public_gate
```

Анкеты, реальные входы/outputs, row-level reports, исходный ГДИС reference и его
история никогда не входят в source snapshot, sdist/wheel, Actions artifacts,
релизы или share-safe handoff. Локальные анкеты создаются отдельно в ignored
private области; публичный код не содержит их генератора или текста вопросов.

Проверять фактический результат: `gh run view RUN_ID --json status,conclusion,jobs,url`,
`gh release view TAG --json url,isPrerelease,tagName,assets`, затем download и SHA.
Наличие workflow YAML, queued run или local Linux build не даёт WINDOWS_PASS.

Основания: [GitHub run_number/rerun](https://docs.github.com/en/actions/reference/workflows-and-actions/contexts),
[GitHub concurrency](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax),
[Windows hard links](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-createhardlinkw),
[uv build excludes](https://docs.astral.sh/uv/concepts/build-backend/).

## Атомарный комплект update-all

Marker записывается в уникальный temporary в том же каталоге: полный JSON,
flush/fsync/close, затем `os.link` (POSIX link / Windows CreateHardLinkW).
Существующее имя не заменяется. При неподдерживаемых links операция fail closed,
без rename/replace fallback; Windows CI проверяет реальную NTFS-семантику гонкой
процессов. На POSIX также fsync каталога; Python не предоставляет такой гарантии
для Windows directory handles. Не заявляется абсолютная стойкость к отключению
питания всех файловых систем: после аварии COMPLETE выводится только из проверки
JSON, обязательных файлов, SHA, source/date/version и полных отчётов.

Cleanup использует сохранённые file identities и затрагивает только собственные
объекты; leftovers не очищаются по маске. Выходной локальный каталог должен
контролироваться пользователем: защита от враждебного процесса, подменяющего имена
между lstat/unlink, не является обещанием этого пользовательского инструмента.
Повторный запуск требует нового каталога/имён и сохраняет чужие объекты.
