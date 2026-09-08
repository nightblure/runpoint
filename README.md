# runpoint

A CLI for running configured Python and Go entry points, with normal and remote
debug launches from the same project configuration.

## Local installation

The project uses Python 3.14 and `uv` for development. The installable package
supports Python 3.10 and newer.

```shell
make sync
```

`uv` installs the dependencies, Hatch builds the package, and local commands are
run through Make:

```shell
make lint
make check
make test
make build
```

## Configuration

`runpoint` looks for `.runpoint.json` or `.runpoint.jsonc` in the current and
parent directories. The file's root must be a list of entry points:

```jsonc
[
  {
    "alias": "tests",
    "command": "pytest",
    "cwd": ".",             // default value. useful for monorepo with any codebase depth
    "venv": ".venv",        // default value
    "load_env_file": false  // default value
  },
  {
    "alias": "go-api",
    "command": "go run ./cmd/api",
    "cwd": "apps/service",
    "env": {"LOG_LEVEL": "debug"}
  },
  {
    "alias": "go-tests",
    "command": "go test ./...",
    "cwd": "apps/service"
  },
  {
    "alias": "go-api-tests",
    "command": "go test ./internal/api",
    "cwd": "apps/service"
  },
  {
    "alias": "go-build",
    "command": "go build ./cmd/api",
    "cwd": "apps/service"
  }
]
```

The runtime is inferred deterministically from the command form. Go commands
must use the full form with the `go` executable: `go test ./...`,
`go run ./cmd/api`, or `go build ./cmd/api`. Runpoint strips the `go` prefix
and substitutes the resolved toolchain binary: `go` from `PATH` for normal
runs, or Delve (`dlv`) for debug runs. Python modules can be written without
`-m`: a bare module name such as `pytest` is launched as `python -m pytest`;
the explicit `python -m pytest` form is also valid. Python scripts are
launched as `python <script>.py`; the `python` prefix is optional — a bare
script path (`scripts/worker.py`) is recognized as Python too. A bare
`-m <module>` (`-m pytest`) is rejected with an error telling you to write
the command explicitly.

Each entry point contains exactly one command. Runpoint always launches without
a shell and rejects unquoted shell composition and redirection operators such
as `&&`, `||`, `;`, `|`, `&`, `<`, and `>`. Configure separate entry points
instead of combining commands.

The `cwd` and `env_file` paths are set relative to the configuration directory.
For Python entry points, the `venv` path is set relative to `cwd`. Go tools are
resolved from `PATH` and do not use `venv`.

Both runtimes inherit the process environment, then apply values from `env`.
Applications can opt into `env_file` with `load_env_file`; test entry points,
including `go test`, cannot load dotenv files.

Configured Go commands must target packages, not concrete `.go` source files,
for `go run`, `go test`, and `go build`. For example, use `go run ./cmd/app`
instead of `go run cmd/app/main.go`, and use `go run .` instead of
`go run main.go`. Passing a file
list to Go compiles only the listed files and can omit sibling files in the same
package. This validation applies only to the configured command; arguments
after runpoint's `--` remain target arguments.

Runpoint does not require or preflight `go.mod` or `go.work`. Package and module
resolution is delegated to the Go toolchain, preserving Go's parent-module
discovery, workspace support, GOPATH operation, and versioned targets where the
selected Go command supports them.

## Usage

```shell
runpoint --help
```

Arguments after `--` are appended to the configured target command:

```shell
# go run ./cmd/api --port 8080
runpoint go-api -- --port 8080

# go test ./... -run TestAPI
runpoint go-tests -- -run TestAPI

# go build ./cmd/api
runpoint go-build
```

Normal Go launches require `go` in `PATH`. Debug launches require `dlv` in
`PATH` and support configured `run` and `test` commands:

```shell
# dlv debug --headless --listen=127.0.0.1:2345 --api-version=2 \
#   --accept-multiclient ./cmd/api -- --port 8080
runpoint go-api --debug --debug-port 2345 -- --port 8080

# dlv test ... ./internal/api -- -test.run TestAPI
runpoint go-api-tests --debug --debug-port 2345 -- -test.run TestAPI
```

The configured Go `run` debug command must contain exactly one target. A Go
`test` debug command can omit its package to use `cwd`, or contain one package.
Application or test-binary arguments belong after runpoint's `--` separator.
Recursive or multi-package patterns such as `./...`, `all`, `std`, `cmd`, and
`tool` remain available in normal mode but cannot be debug-launched by Delve.
`--no-debug-wait` adds Delve's `--continue` for Go `run`. Delve does not support
that behavior for `test`, so runpoint rejects that combination. Go `build`
cannot be debug-launched. `--debug-subprocesses` is Python-specific and is
rejected when explicitly supplied for a Go entry point.
