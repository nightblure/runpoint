# Послойный рефакторинг CLI

Status: approved
Date: 2026-09-07
Slug: cli-layer-refactoring

## Statement

Разделить монолитный `src/runpoint/cli.py` на пять минимальных модулей: CLI, use-cases, сервисы, доступ к данным и домен. Это только структурный рефакторинг: должны сохраниться команды и опции Typer, тексты и порядок пользовательского вывода и ошибок, коды выхода, поиск конфигурации, JSON/JSONC-схема и значения по умолчанию, порядок источников окружения, формирование команды и замена текущего процесса через `execvpe`.

Console entry point остаётся `runpoint.cli:main`; запуск через `python -m runpoint` также остаётся рабочим. Перенесённый Python API не реэкспортируется из `runpoint.cli`: тесты и внутренние импорты переходят на модули-владельцы. `README.md` не изменяется.

Критерии приёмки:

- `runpoint --list`, запуск по алиасу, debug-опции, `--no-env` и передача target-аргументов после первого `--` ведут себя как до рефакторинга;
- `.runpoint.json` сохраняет приоритет над `.runpoint.jsonc`, включая поиск по родительским каталогам; формат, валидация и ошибки конфигурации не меняются;
- приоритет окружения остаётся `dotenv < os.environ < entrypoint.env`, а тестовые entry points и `--no-env` не читают dotenv;
- обычная и debugpy-команды, проверка путей и `os.execvpe` сохраняют текущую семантику;
- `pyproject.toml` по-прежнему содержит `runpoint = "runpoint.cli:main"`, новые реэкспорты совместимости отсутствуют;
- `make test`, `make check` и `make build` проходят.

## Decisions and open questions

- Использовать ровно по одному модулю на слой в существующем пакете: `cli.py`, `use_cases.py`, `services.py`, `data.py`, `domain.py`. Подпакеты, repository-классы, Protocol/ABC, контейнер внедрения зависимостей и новые библиотеки не нужны.
- `Entrypoint` остаётся единственной доменной сущностью. `LauncherContext` остаётся в CLI, потому что служит объектом `typer.Context`, а не доменной моделью. Новые request/result-классы не вводятся.
- `run` сохраняет Typer-сигнатуру, обработку `--list`, отсутствие алиаса и неизвестный алиас, после чего передаёт выбранный `Entrypoint` в один use-case `launch_entrypoint`.
- `launch_entrypoint` получает параметры запуска явно и координирует сервисы в прежнем порядке. Для отделения Rich/Typer от application-слоя он получает две функции вывода: обычных сообщений и debug-статуса в stderr. Это две функции, а не новый интерфейс или класс.
- `load_env_variables` остаётся сервисом и сохраняет возврат `dict[str, str]`, но больше не печатает напрямую. Use-case выводит уведомление после успешной загрузки, сохраняя прежний порядок и не связывая сервис с Rich.
- Низкоуровневые `flush`, `chdir` и `execvpe` выделяются в сервис `replace_process`; shell по-прежнему не используется, новый subprocess не создаётся.
- Внутренние импорты делаются через модули-владельцы или конкретные требуемые символы, но перенесённые имена намеренно не импортируются в `cli.py` ради совместимости. Разрешённый перенос означает, например, `runpoint.domain.entrypoint_factory`, а не прежний `runpoint.cli.entrypoint_factory`.
- Конфигурационная схема не выносится в отдельные DTO/schema-классы: текущая проверка словарей и построение `Entrypoint` достаточны.
- Репозиторий не содержит Git metadata. Перед реализацией нужен временный снимок затрагиваемых файлов вне рабочего дерева; это операционная страховка, а не часть production-кода.

Блокирующих открытых вопросов нет. Документ одобрен для реализации.

## Code context

- `src/runpoint/cli.py:25-140` совмещает доменную модель, её правила и фабрику преобразования конфигурационных значений.
- `src/runpoint/cli.py:143-157` ищет ближайший конфиг, а `src/runpoint/cli.py:213-369` читает JSONC, проверяет схему, непустой список и уникальность алиасов.
- `src/runpoint/cli.py:160-210` разрешает рабочую директорию, Python executable и файловый target.
- `src/runpoint/cli.py:396-428` читает dotenv и объединяет окружение; текущий приоритет задаётся последовательностью обновлений на строках 420-427.
- `src/runpoint/cli.py:431-466` формирует обычную либо debugpy-команду.
- `src/runpoint/cli.py:469-477` реализует автодополнение, `src/runpoint/cli.py:479-534` задаёт публичный Typer-контракт.
- `src/runpoint/cli.py:535-600` одновременно выбирает entry point, оркестрирует запуск, форматирует вывод и заменяет процесс.
- `src/runpoint/cli.py:603-629` задаёт bootstrap: приоритет имён конфигурации, разделение аргументов и вызов Typer.
- `pyproject.toml:25-26` фиксирует внешний console entry point; он не меняется.
- `src/runpoint/__main__.py:5-8` импортирует и вызывает `runpoint.cli.main`; этот контракт не меняется.
- `tests/test_cli.py:9-18` сейчас импортирует весь проверяемый Python API из монолита. Доменные проверки находятся на строках 21-82, конфигурационные — 85-135 и 338-383, сервисные — 137-324 и 386-414, разбор CLI-аргументов — 326-335.
- `README.md:23-41` документирует поиск конфигурации и относительность путей, `README.md:43-51` — пользовательские команды. Файл служит контрактным свидетельством, но не изменяется.
- Базовое состояние на 2026-09-07: `make test` — 38 passed; `make check` — форматирование, Ruff и ty проходят.

Целевая карта символов:

| Модуль | Владелец символов |
| --- | --- |
| `runpoint.domain` | `KNOWN_TEST_RUNNERS`, `Entrypoint`, `entrypoint_factory` |
| `runpoint.data` | `find_config_path`, `load_jsonc`, `_require_type`, `_require_string_mapping`, `_parse_entrypoint`, `load_entrypoints`, `dotenv_values` |
| `runpoint.services` | `resolve_working_directory`, `resolve_python_executable`, `validate_target`, `load_env_variables`, `build_command`, новый `replace_process` из текущих строк 595-600 |
| `runpoint.use_cases` | новый `launch_entrypoint`, извлечённый из ветки запуска текущего `run` |
| `runpoint.cli` | `DEFAULT_DEBUG_PORT`, `LauncherContext`, `split_launcher_and_target_args`, `print_entrypoints`, `complete_alias`, функции Rich-вывода, `app`, `run`, `main` |

Направление зависимостей ациклично:

```text
cli -> use_cases -> services -> data -> domain
 |          |           |                  ^
 +----------+-----------+------------------+
```

Прямые зависимости внешнего слоя на более глубокие допустимы (`cli` загружает конфигурацию через `data` и хранит `domain.Entrypoint`), обратные запрещены. `domain` использует только стандартную библиотеку; `data` не знает о CLI/use-cases; `services` не знает о Typer/Rich; `use_cases` не знает о Typer и получает функции вывода от `cli`.

## Chosen solution

Выполнить механическое перемещение существующих функций по карте и извлечь только две функции, необходимые для границ слоёв: `launch_entrypoint` и `replace_process`. `cli.run` останется адаптером Typer: список/выбор алиаса и делегирование. Use-case сохранит текущую последовательность разрешения путей, печати, сборки окружения и `execvpe`; сервисы выполнят отдельные операции; data-модуль останется конкретным файловым адаптером без абстрактного repository.

Это минимальный вариант, потому что существующее приложение локальное, синхронное и имеет единственный файловый источник данных. Дополнительные сущности, порты и подпакеты не дадут текущей задаче проверяемой пользы. Разрешение переноса Python API исключает compatibility-обвязку в `cli.py`.

## Implementation stages

### Stage 1: Домен и конфигурация имеют собственных владельцев

- Actions: добавить `src/runpoint/domain.py` и `src/runpoint/data.py`; переместить в них символы согласно карте без изменения условий, значений по умолчанию и текстов ошибок; переключить `cli.py` на новые владельцы; разделить соответствующие проверки из `tests/test_cli.py` в `tests/test_domain.py` и `tests/test_data.py`. Не менять `README.md`, `pyproject.toml` и `__main__.py`.
- Check: доменные параметризованные случаи принимают и отклоняют те же команды; JSONC с URL и комментариями загружается с прежними defaults; пустая/невалидная конфигурация, неизвестные поля, неверный `env` и дубли алиасов дают прежние сообщения; поиск из вложенного каталога возвращает ближайший файл. Полный набор тестов и `make check` проходят.
- TDD seam: публичные для проекта функции `runpoint.domain.entrypoint_factory`, `runpoint.data.load_entrypoints` и `runpoint.data.find_config_path`; существующие тестовые входы переносятся до перемещения implementation.
- Dependencies: none.
- Rollback: из-за отсутствия Git восстановить `cli.py` и тесты из временного снимка, удалить два новых модуля, затем повторить базовые `make test` и `make check`.

### Stage 2: Runtime-операции находятся в сервисном слое

- Actions: добавить `src/runpoint/services.py`; переместить разрешение путей, проверку target, загрузку окружения и сборку команды; извлечь `replace_process` с прежними flush/chdir/execvpe; сохранить возврат `dict[str, str]` из `load_env_variables`, убрав только Rich-вывод; временно вызвать эти сервисы из существующей оркестрации `cli.run`; перенести проверки в `tests/test_services.py`.
- Check: обычная и debugpy-команды побайтно совпадают с текущими списками аргументов; fallback `bin/python3`, принятие `-m`, ошибка отсутствующего скрипта, пропуск dotenv и приоритет `dotenv < process < entrypoint` сохраняются; notice для `--no-env`, тестового запуска и успешно загруженного dotenv совпадает с текущим текстом. Полный набор тестов и `make check` проходят.
- TDD seam: функции `runpoint.services.build_command`, `load_env_variables`, `resolve_python_executable`, `validate_target`; `replace_process` проверяется позднее через подмену на границе use-case, реальный `execvpe` в тестах не вызывается.
- Dependencies: Stage 1.
- Rollback: восстановить pre-stage `cli.py` и тесты из временного снимка, удалить `services.py`, сохранив одобренные файлы Stage 1.

### Stage 3: CLI делегирует запуск одному use-case

- Actions: добавить `src/runpoint/use_cases.py` с `launch_entrypoint`; перенести туда последовательность resolve/validate/build-env/replace-process; принимать функции обычного и stderr-вывода из `cli.py`; оставить в `run` обработку Typer (`--list`, missing/unknown alias) и делегирование; добавить `tests/test_use_cases.py`, а в `tests/test_cli.py` оставить CLI-контракт и parsing первого `--`. Убедиться, что перенесённые API не импортированы обратно в `cli.py` и не добавлены в `__init__.py`.
- Check: тест use-case с подменённым `replace_process` получает прежние `working_dir`, command и environment, а сообщения возникают в прежнем порядке; debug-статус уходит через stderr-функцию непосредственно перед заменой процесса. Typer-проверки подтверждают сортированный `--list`, прежние ошибки отсутствующего/неизвестного алиаса и передачу аргументов после первого `--`. `runpoint.cli:main` и `python -m runpoint` используют прежний bootstrap; `make test`, `make check` и `make build` проходят.
- TDD seam: `runpoint.use_cases.launch_entrypoint` с подменой `runpoint.services.replace_process` и собирающими вывод функциями; публичная CLI-грань — `runpoint.cli.app` через `typer.testing.CliRunner`, финальная упаковочная грань — неизменный `runpoint.cli:main`.
- Dependencies: Stage 2.
- Rollback: восстановить pre-stage `cli.py`, `services.py` и тесты из временного снимка, удалить `use_cases.py`, сохранив одобренные результаты Stages 1-2.

## Testing strategy

- Перед каждым перемещением сначала зафиксировать/перенести соответствующие characterization-тесты и получить ожидаемый RED только из-за ещё отсутствующего нового модуля, затем переместить код до GREEN. Не переписывать проверки так, чтобы они ослабляли тексты ошибок или точные списки команд.
- `tests/test_domain.py`: распознавание test runners, нормальные команды, все текущие ошибки команды и запрет dotenv для тестового entry point.
- `tests/test_data.py`: JSONC/comments, defaults и пути, структурная валидация, неизвестные поля, строковый `env`, пустой список, дубли и ближайший родительский конфиг.
- `tests/test_services.py`: обычная/debugpy-команда, target args, разрешение cwd/interpreter/target, dotenv skip/read и приоритет окружения. Проверять также notice, заменяющий нынешний прямой Rich-вывод.
- `tests/test_use_cases.py`: один обычный и один debug-сценарий с временным файловым деревом либо подменёнными сервисами; запрет реального `execvpe`; точные аргументы `replace_process` и порядок информационных/stderr-сообщений.
- `tests/test_cli.py`: сохранить тест первого `--`; добавить узкие Typer-characterization-тесты для `--list`, missing alias и unknown alias, а делегирование проверять подменой `launch_entrypoint`, не дублируя сервисные тесты.
- Финальная проверка: `source .venv/bin/activate && make test`, затем `make check` и `make build`. Проверить содержимое wheel на наличие всех пяти модулей и неизменность entry point в metadata.

## Risks

- Главный риск — незаметно изменить порядок побочных эффектов: сейчас `working_dir` и command печатаются до чтения dotenv, debug-статус — после формирования окружения, а flush выполняется прямо перед `chdir/execvpe`. Контракт `launch_entrypoint` и его тест должны закрепить этот порядок.
- Простое перемещение меняет пути Python-импортов и `Entrypoint.__module__`. Это сознательно принято; compatibility-реэкспорты запрещены, а все известные потребители находятся в `tests/test_cli.py`.
- Нельзя вызвать реальный `execvpe` в unit-тесте: он заменит pytest-процесс. Граница `services.replace_process` должна подменяться в тесте use-case.
- Возможны циклические импорты, если `LauncherContext` или функции Rich-вывода будут вынесены внутрь. Зафиксированное направление зависимостей исключает импорт `cli` из остальных четырёх модулей.
- Из-за отсутствия Git ошибочное промежуточное перемещение нельзя откатить обычной командой VCS. Временные снимки и полная проверка после каждого рабочего stage обязательны.
