"""Реализует командный интерфейс runpoint."""

from __future__ import annotations

import dataclasses
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, cast

import typer
from rich import print as rich_print

from runpoint import data, domain, use_cases

if TYPE_CHECKING:
    from collections.abc import Sequence


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class LauncherContext:
    """Хранит контекст текущего запуска CLI."""

    config_dir: Path
    target_args: tuple[str, ...]
    entrypoints: tuple[domain.Entrypoint, ...]


def split_launcher_and_target_args(
    argv: Sequence[str],
) -> tuple[list[str], list[str]]:
    """Разделяет аргументы runpoint и запускаемой команды."""
    args = list(argv)

    try:
        separator = args.index("--")
    except ValueError:
        return args, []

    return args[:separator], args[separator + 1 :]


def print_entrypoints(entrypoints: Sequence[domain.Entrypoint]) -> None:
    """Выводит доступные точки входа."""
    width = max((len(entrypoint.alias) for entrypoint in entrypoints), default=0)

    for entrypoint in sorted(entrypoints, key=lambda item: item.alias):
        kind = "test" if entrypoint.is_test() else "app"
        cwd_note = "" if entrypoint.cwd == Path() else f"  (cwd={entrypoint.cwd})"
        rich_print(
            f"{entrypoint.alias:<{width}}  [{kind}]  {entrypoint.command}{cwd_note}"
        )


def _print_message(message: str) -> None:
    rich_print(message)


def _print_debug(message: str) -> None:
    rich_print(message, file=sys.stderr, flush=True)


def complete_alias(ctx: typer.Context, incomplete: str) -> list[str]:
    """Возвращает алиасы, подходящие для автодополнения."""
    launcher_context = cast("LauncherContext", ctx.obj)
    return [
        entrypoint.alias
        for entrypoint in launcher_context.entrypoints
        if entrypoint.alias.startswith(incomplete)
    ]


app = typer.Typer(pretty_exceptions_enable=False)


@app.command(
    help="Запускает одну точку входа Python или Go",
    epilog=(
        "Аргументы точки входа передаются после '--', например: "
        "runpoint testcur -- -k test_name"
    ),
)
def run(  # noqa: PLR0913, PLR0917 -- signature defines the Typer CLI
    ctx: typer.Context,
    alias: Annotated[
        str | None,
        typer.Argument(help="алиас точки входа", autocompletion=complete_alias),
    ] = None,
    list_entrypoints: Annotated[  # noqa: FBT002 -- Typer boolean option
        bool,
        typer.Option("--list", help="показать зарегистрированные точки входа и выйти"),
    ] = False,
    debug: Annotated[  # noqa: FBT002 -- Typer boolean option
        bool,
        typer.Option(
            "--debug",
            help="запустить через debugpy или Delve и принять подключение от IDE",
        ),
    ] = False,
    debug_port: Annotated[
        int | None,
        typer.Option(
            "--debug-port",
            metavar="PORT",
            help="порт отладочного сервера",
        ),
    ] = None,
    no_debug_wait: Annotated[  # noqa: FBT002 -- Typer boolean option
        bool,
        typer.Option(
            "--no-debug-wait",
            help="запустить target-код без ожидания подключения IDE",
        ),
    ] = False,
    # debug_subprocesses не учитываем и оставляем включенным ВСЕГДА по умолчанию,
    # потому что заранее неизвестно пригодится он или нет
    debug_subprocesses: Annotated[  # noqa: FBT002 -- Typer boolean option
        bool,
        typer.Option(
            "--debug-subprocesses",
            help="подключать debugpy к дочерним Python-процессам",
        ),
    ] = True,
    no_env: Annotated[  # noqa: FBT002 -- Typer boolean option
        bool,
        typer.Option("--no-env", help="не загружать .env"),
    ] = False,
) -> None:
    """Запускает выбранную точку входа."""
    launcher_ctx = cast("LauncherContext", ctx.obj)
    entrypoints = launcher_ctx.entrypoints
    target_args = launcher_ctx.target_args

    if list_entrypoints:
        print_entrypoints(entrypoints)
        return

    alias_to_entrypoint = {entrypoint.alias: entrypoint for entrypoint in entrypoints}

    if alias is None:
        ctx.fail("Нужно указать алиас точки входа; доступные алиасы: --list")

    if alias not in alias_to_entrypoint:
        ctx.fail(f"Алиас {alias!r} не найден")

    entrypoint = alias_to_entrypoint[alias]
    debug_subprocesses_source = ctx.get_parameter_source("debug_subprocesses")

    if (
        entrypoint.runtime is domain.Runtime.GO
        and debug_subprocesses_source is not None
        and debug_subprocesses_source.name == "COMMANDLINE"
    ):
        ctx.fail("--debug-subprocesses не поддерживается для Go")

    use_cases.launch_entrypoint(
        config_dir=launcher_ctx.config_dir,
        entrypoint=entrypoint,
        target_args=target_args,
        debug=debug,
        debug_port=debug_port,
        no_debug_wait=no_debug_wait,
        debug_subprocesses=debug_subprocesses,
        no_env=no_env,
        print_message=_print_message,
        print_debug=_print_debug,
    )


def main() -> None:
    """Находит конфигурацию и запускает Typer-приложение."""
    config_path: Path | None = None
    cwd = Path.cwd()

    for cfg_filename in (".runpoint.json", ".runpoint.jsonc"):
        config_path = data.find_config_path(cwd=cwd, cfg_filename=cfg_filename)

        if config_path is not None:
            break

    if config_path is None:
        message = (
            f"Файл конфигурации {cfg_filename} не найден в {cwd} "
            "и родительских директориях"
        )
        raise SystemExit(message)

    entrypoints = data.load_entrypoints(config_path)

    launcher_args, target_args = split_launcher_and_target_args(sys.argv[1:])
    launcher_context = LauncherContext(
        config_dir=config_path.parent,
        entrypoints=entrypoints,
        target_args=tuple(target_args),
    )
    app(launcher_args, obj=launcher_context)


if __name__ == "__main__":
    main()
