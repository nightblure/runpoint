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

Поднять версию и запушить тег (перед пушом тега лучше убедиться, что CI зеленый):

```shell
make release_patch
make push_tag
# OR
make release_minor
make push_tag
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
