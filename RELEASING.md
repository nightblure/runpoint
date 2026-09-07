# Выпуск релиза

Релизы `runpoint` автоматически публикуются в GitHub Releases без отправки пакета в PyPI. Workflow `.github/workflows/ci-release.yml` проверяет проект, собирает wheel и sdist и прикладывает их к релизу.

## Требования

- есть право создавать и отправлять теги в репозиторий;
- рабочее окружение синхронизировано через `uv`;
- все изменения выпуска смержены в `main`.

## 1. Подготовить версию

Установить новую версию в `pyproject.toml`:

```toml
[project]
version = "0.2.0"
```

Обновить lock-файл и выполнить локальные проверки:

```shell
source .venv/bin/activate
uv lock
make check
make test
```

Изменения `pyproject.toml` и `uv.lock` должны попасть в pull request и быть смержены в `main` до создания тега.

В pull request workflow автоматически запускает `make check` и `make test`. Сливать изменения следует только после успешного завершения job `Quality checks`.

## 2. Обновить main

После merge получить актуальное состояние ветки:

```shell
git switch main
git pull --ff-only origin main
git status --short
```

Перед продолжением `git status --short` не должен выводить изменений.

## 3. Проверить версию

Получить версию пакета из `pyproject.toml`:

```shell
source .venv/bin/activate
VERSION="$(uv run python -c 'import pathlib, tomllib; print(tomllib.loads(pathlib.Path("pyproject.toml").read_text())["project"]["version"])')"
test -n "${VERSION}"
```

Убедиться, что тег этой версии ещё не существует:

```shell
git tag --list "v${VERSION}"
```

Команда не должна вывести тег.

## 4. Создать тег

Теги релизов имеют формат `v<version>`, например `v0.2.0`:

```shell
git tag -a "v${VERSION}" -m "runpoint v${VERSION}"
git push origin "v${VERSION}"
```

Тег должен указывать на проверенный commit ветки `main`. Push тега `v*` запускает оба job workflow:

- `Quality checks` устанавливает locked-зависимости, выполняет `make check` и `make test`;
- `Create GitHub release` запускается только после успешных проверок.

## 5. Дождаться GitHub Release

Следить за запуском можно на вкладке Actions либо через GitHub CLI:

```shell
gh run list --workflow "CI and release" --limit 5
```

Release job выполняет следующие действия:

1. Проверяет, что тег `v${VERSION}` совпадает с `project.version`.
2. Собирает дистрибутивы через `make build` в чистом runner.
3. Создаёт GitHub Release с автоматически сформированными release notes.
4. Прикладывает содержимое `dist/` к релизу.

Workflow не содержит публикации в PyPI или другой package index.

## 6. Проверить публикацию

```shell
gh release view "v${VERSION}"
```

Нужно проверить:

- тег и заголовок содержат ожидаемую версию;
- релиз указывает на правильный commit;
- к релизу приложены wheel и sdist;
- оба файла имеют версию, совпадающую с тегом.

Ожидаемые artifacts:

```text
runpoint-${VERSION}-py3-none-any.whl
runpoint-${VERSION}.tar.gz
```

## Ошибка до публикации

Если workflow завершился ошибкой, GitHub Release не создаётся. Нужно открыть упавший job в Actions и исправить причину.

При несовпадении тега и `project.version` нельзя просто перезапускать job: сначала необходимо привести версию и тег в соответствие. Если ошибочный тег ещё не был опубликован как Release, его можно удалить и создать заново:

```shell
git tag -d "v${VERSION}"
git push origin --delete "v${VERSION}"
```

Удаление опубликованного тега требует отдельного решения и не должно выполняться как обычная часть процесса.

## Ошибка после публикации

Если релиз уже опубликован и обнаружена ошибка, не следует переиспользовать или переписывать опубликованный тег. Нужно исправить проблему и выпустить следующую patch-версию, например `0.2.1`.
