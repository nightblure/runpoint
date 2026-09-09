# runpoint
[![Hatch project](https://img.shields.io/badge/%F0%9F%A5%9A-Hatch-4051b5.svg)](https://github.com/pypa/hatch)
[![uv](https://img.shields.io/badge/uv-261230.svg?logo=uv&logoColor=#de5fe9)](https://docs.astral.sh/uv/)
[![ty](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ty/main/assets/badge/v0.json)](https://github.com/astral-sh/ty)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)

CLI для запуска настроенных точек входа Python и Go в обычном режиме или с
подключением отладчика.

## Конфигурация

**Runpoint** ищет `.runpoint.json` или `.runpoint.jsonc` в текущей и родительских
директориях. Корень файла должен содержать список точек входа:

```jsonc
[
  {
    "alias": "tests",
    "command": "pytest"
  },
  {
    "alias": "api",
    "command": "python -m app",
    "cwd": "services/api",
    "venv": ".venv",
    "load_env_file": true,
    "env_file": ".env",
    "env": { "LOG_LEVEL": "debug" }
  },
  {
    "alias": "go-api",
    "command": "go run ./cmd/api",
    "cwd": "services/go-api"
  }
]
```

Пути `cwd` и `env_file` задаются относительно директории конфигурации, а
`venv` для Python — относительно `cwd`. Переменные из `env` имеют приоритет над
окружением процесса и `.env`. Для тестовых точек входа `.env` не загружается.

Каждая точка входа запускает одну команду без shell-операторов. Для Go указывайте
пакет, а не отдельный `.go`-файл.

## Запуск

```shell
runpoint --help
runpoint --list
runpoint tests -- -k test_name
runpoint go-api -- --port 8080
```

Аргументы после `--` передаются запускаемой программе.

## Отладка

```shell
# Python, порт по умолчанию 5678
runpoint api --debug

# Go, порт по умолчанию 2345
runpoint go-api --debug -- --port 8080

# Сменить порт отладочного сервера
runpoint api --debug --debug-port 7000
```

Python всегда запускается через `debugpy` с ожиданием подключения IDE (`--wait-for-client`) и подключением дочерних
процессов (`--configure-subProcess
True`). Delve запускается в single-client режиме: после отключения IDE он
самостоятельно завершает сервер и target, поэтому `--accept-multiclient` и
`--continue` не нужны.

Для обычного запуска Go нужен `go` в `PATH`, для отладки — `dlv`. В режиме
отладки Delve поддерживаются `go run` и `go test` с одним пакетом; `go build` и
шаблоны нескольких пакетов не поддерживаются.

Перед запуском и после его завершения Runpoint освобождает порт от зависшего
отладчика через `psutil`: завершаются только процессы с точным совпадением порта —
`debugpy adapter` для Python (по `--port`) и headless `dlv`/`delve` для Go (по
`--listen`). Посторонние процессы и обычные `python -m debugpy` target-процессы не
трогаются. Если порт занят не отладчиком, запуск завершится штатной ошибкой
отладочного сервера; освободите порт вручную или передайте другой через
`--debug-port`.

При отладке Python Runpoint запускает `debugpy` дочерним процессом в той же
foreground-группе, поэтому Ctrl-C доходит до target, а Runpoint ждёт завершения
child и возвращает код 130 по SIGINT.

Подключение дочерних Python-процессов включено всегда; отображение таких сессий
зависит от IDE. В Zed для этого остаётся известный lifecycle-баг (#55706).

### Zed

Создать `.zed/debug.json` в запускаемом проекте:

```jsonc
// https://zed.dev/docs/languages/go#debugging
// https://zed.dev/docs/languages/python#debugging
// https://zed.dev/docs/languages/python#debug-a-flask-app
[
  {
    "label": "go_debug",
    "adapter": "Delve",
    "request": "attach",
    "mode": "remote",
    "tcp_connection": {
      "host": "127.0.0.1",
      "port": 2345,
    },
    "justMyCode": false,
    "subProcess": true,
  },
  {
    "label": "py_debug",
    "type": "python",
    "adapter": "Debugpy",
    "request": "attach",
    "connect": { 
      "host": "127.0.0.1",
      "port": 5678 
    },
    "justMyCode": false,
    "subProcess": true,
    // not tested!
    "autoReload": { "enable": true },
  },
]
```

### PyCharm

Создайте конфигурацию **Attach to DAP** и укажите адрес `127.0.0.1:5678` или
порт, переданный через `--debug-port`.

### GoLand

Создайте конфигурацию **Go Remote**, укажите порт `2345` или порт из
`--debug-port` и выберите **On disconnect: Stop remote Delve process**.
