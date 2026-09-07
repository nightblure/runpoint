# runpoint

CLI для запуска настроенных Python entry points в виртуальных окружениях проектов.

## Локальная установка

Проект требует Python 3.14 и `uv`.

```shell
make sync
```

`uv` устанавливает зависимости, Hatch собирает пакет, а локальные команды запускаются
через Make:

```shell
make lint
make check
make test
make build
```

## Конфигурация

`runpoint` ищет `.runpoint.json` или `.runpoint.jsonc` в текущей и родительских
директориях. Корнем файла должен быть список точек входа:

```json
[
  {
    "alias": "tests",
    "command": "-m pytest",
    "cwd": ".",
    "venv": ".venv",
    "load_env_file": false
  }
]
```

Пути `cwd` и `env_file` задаются относительно директории конфигурации. Путь
`venv` задаётся относительно `cwd`.

## Использование

```shell
runpoint --list
runpoint <alias>
runpoint <alias> -- --queue priority --limit 10
runpoint <alias> --debug
runpoint <alias> --debug --debug-port 5679 --no-debug-wait
runpoint --install-completion
```
