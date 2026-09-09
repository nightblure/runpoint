# Выпуск релиза

GitHub workflow публикует релизы `runpoint` без отправки пакета в PyPI.

## 1. Обновить версию

Указать версию явно либо увеличить текущую:

```shell
source .venv/bin/activate
uv version 0.2.0
# или: uv version --bump patch
```

`uv` обновит `pyproject.toml` и `uv.lock`. Выполнить проверки, закоммитить изменения и смержить их в `main`:

```shell
make check
make test
```

## 2. Создать тег

После merge обновить локальную ветку и создать тег из версии проекта:

```shell
git switch main && git pull

make bump_patch && uv lock
# or
make bump_minor && uv lock

git add . && git commit -m "bump version" && git push
VERSION="$(uv version --short)"
git tag -a "v${VERSION}" -m "runpoint v${VERSION}"
git push origin "v${VERSION}"
```

Удаление тегов:
```shell
git tag -d <tag>
git push --delete origin <tag>
```

Тег должен совпадать с `project.version`. Push тега `v*` запускает проверки, сборку wheel/sdist и создание GitHub Release. В примечания к релизу автоматически добавляется команда установки текущего wheel.

## 3. Проверить релиз

```shell
gh run list --workflow "CI and release" --limit 5
gh release view "v${VERSION}"
```

Опубликованные теги не переписываются. Исправления выпускаются следующей patch-версией.
