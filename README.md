# runpoint

A CLI for running configured Python entry points in project virtual environments.

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
    "command": "-m pytest",
    "cwd": ".",             // default value. useful for monorepo with any codebase depth
    "venv": ".venv",        // default value
    "load_env_file": false  // default value
  }
]
```

The `cwd` and `env_file` paths are set relative to the configuration directory.
The `venv` path is set relative to `cwd`.

## Usage

```shell
runpoint --help
```
