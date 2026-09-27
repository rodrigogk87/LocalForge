"""CLI de LocalForge.

    localforge health
    localforge ask <repo> "<objetivo>"

Interfaz minima a proposito: la UI no importa hasta que el harness funcione.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

from localforge.config import DOTENV_APPLIED, find_dotenv, settings
from localforge.harness import AgentHarness
from localforge.harness.context import ContextBreakdown, ContextBudget
from localforge.models import AgentTask, ModelResponse, ToolCall, ToolResult
from localforge.providers import build_provider
from localforge.providers.base import ProviderError
from localforge.tools import default_registry

DIM = "\033[2m"
BOLD = "\033[1m"
CYAN = "\033[36m"
GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
OFF = "\033[0m"


def _init_console() -> bool:
    """La consola de Windows usa cp1252 y revienta con cualquier glifo no-latin1.

    Intentamos pasarla a UTF-8; si no se puede, devolvemos False y la salida
    cae a marcas ASCII. Un agente que muere imprimiendo un tilde es un agente
    que "falla" por una razon que no tiene nada que ver con el agente.
    """
    ok = True
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, OSError, ValueError):
            ok = False
    return ok


UNICODE_OK = _init_console()

OK_MARK = "✓" if UNICODE_OK else "[ok]"
BAD_MARK = "✗" if UNICODE_OK else "[x]"
ARROW = "→" if UNICODE_OK else "->"
RULE = "──" if UNICODE_OK else "--"
PIPE = "│" if UNICODE_OK else "|"


def _supports_color() -> bool:
    return sys.stdout.isatty()


def _c(text: str, color: str) -> str:
    return f"{color}{text}{OFF}" if _supports_color() else text


# ---------------------------------------------------------------------------


class ConsoleSink:
    """Muestra el loop mientras corre.

    Un agente que tarda un minuto sin decir nada es indistinguible de uno
    colgado. Ver las tool calls en vivo es la observabilidad minima.
    """

    def __init__(self, verbose: bool = False) -> None:
        self.verbose = verbose
        self._t0 = time.monotonic()

    def __call__(self, event: str, **payload: object) -> None:
        stamp = _c(f"[{time.monotonic() - self._t0:6.1f}s]", DIM)

        if event == "turn_start":
            print(f"{stamp} {_c(RULE + ' turno ' + str(payload['turn']), BOLD)}")

        elif event == "context_built":
            bd = payload["breakdown"]
            assert isinstance(bd, ContextBreakdown)
            # Sin verbose, una linea: lo que se gasto y si hubo que compactar.
            warn = bd.pct >= 75
            line = f"{bd.total}/{bd.available} tok ({bd.pct:.0f}%)"
            if bd.compacted_messages:
                line += f" · compactado {bd.compacted_messages} obs (-{bd.recovered_tokens} tok)"
            print(f"{stamp}   ctx: {_c(line, YELLOW if warn else DIM)}")
            if self.verbose:
                for row in bd.table().splitlines()[1:]:
                    print(f"{stamp} {_c(row, DIM)}")

        elif event == "model_response":
            response = payload["response"]
            assert isinstance(response, ModelResponse)
            meta = _c(
                f"{response.input_tokens}{ARROW}{response.output_tokens} tok | {response.duration_ms / 1000:.1f}s",
                DIM,
            )
            print(f"{stamp}   modelo: {response.stop_reason.value} · {meta}")
            if response.content and self.verbose:
                head = response.content.strip().splitlines()[:3]
                for line in head:
                    print(f"{stamp}   {_c(PIPE + ' ' + line[:110], DIM)}")

        elif event == "verified":
            verdict = payload["verdict"]
            if verdict.ok:
                print(f"{stamp}   {_c(OK_MARK, GREEN)} verificado ({verdict.check})")
            else:
                print(f"{stamp}   {_c(BAD_MARK + ' rechazado por ' + verdict.check, YELLOW)}")
                if self.verbose:
                    print(f"{stamp}   {_c(PIPE + ' ' + verdict.feedback[:150], DIM)}")

        elif event == "tools_start":
            calls = payload["calls"]
            assert isinstance(calls, list)
            for call in calls:
                assert isinstance(call, ToolCall)
                args = ", ".join(f"{k}={v!r}" for k, v in call.arguments.items())
                print(f"{stamp}   {_c(ARROW, CYAN)} {call.name}({args[:100]})")

        elif event == "tools_done":
            results = payload["results"]
            assert isinstance(results, list)
            for result in results:
                assert isinstance(result, ToolResult)
                if result.success:
                    size = len(result.output or "")
                    mark = _c(OK_MARK, GREEN)
                    extra = _c(f"{size} chars{' · truncado' if result.truncated else ''}", DIM)
                    print(f"{stamp}   {mark} {result.name} {extra}")
                else:
                    print(f"{stamp}   {_c(BAD_MARK, RED)} {result.name}: {result.error}")


# ---------------------------------------------------------------------------


async def cmd_health() -> int:
    provider = build_provider()
    try:
        info = await provider.health()
    except ProviderError as exc:
        print(_c(f"{BAD_MARK} {exc}", RED))
        return 1
    finally:
        await provider.aclose()

    print(_c(f"{OK_MARK} inferencia local operativa", GREEN))
    for key, value in info.items():
        print(f"  {key:18} {value}")
    print(f"  {'num_ctx':18} {settings.num_ctx}")
    budget = ContextBudget.from_settings(settings)
    print(f"  {'ctx disponible':18} {budget.available} (reserva {budget.reserve_output} para la salida)")
    print(f"  {'max_turns':18} {settings.max_turns}")
    print(f"  {'wall_clock_s':18} {settings.wall_clock_s:g}s")

    # De donde salio la config: sin esto, un .env que no se esta leyendo es
    # indistinguible de uno que se lee y dice lo mismo.
    origin = find_dotenv()
    if origin:
        print(f"  {'config':18} {origin} ({len(DOTENV_APPLIED)} vars aplicadas)")
    else:
        print(f"  {'config':18} defaults del codigo + entorno (sin .env)")
    return 0


async def cmd_ask(repo: str, objective: str, *, verbose: bool, max_turns: int) -> int:
    repo_path = Path(repo).expanduser().resolve()
    if not repo_path.is_dir():
        print(_c(f"{BAD_MARK} '{repo}' no es un directorio", RED))
        return 1

    provider = build_provider()
    try:
        info = await provider.health()
    except ProviderError as exc:
        print(_c(f"{BAD_MARK} {exc}", RED))
        await provider.aclose()
        return 1

    registry = default_registry()
    harness = AgentHarness(provider, registry, on_event=ConsoleSink(verbose))

    task = AgentTask(
        objective=objective,
        repo_path=str(repo_path),
        max_turns=max_turns,
        wall_clock_s=settings.wall_clock_s,
        token_budget=settings.token_budget,
    )

    print(f"{_c('repo', DIM)}   {repo_path}")
    print(f"{_c('modelo', DIM)} {info['model']} @ {info['host']}")
    print(f"{_c('tools', DIM)}  {', '.join(registry.names())}")
    print(f"{_c('tarea', DIM)}  {objective}")
    print()

    try:
        outcome = await harness.run(task)
    finally:
        await provider.aclose()

    print()
    color = GREEN if outcome.succeeded else RED
    print(_c(f"{RULE} {outcome.summary()}", color))
    if outcome.state_path:
        print(_c(f"   estados: {outcome.state_path}", DIM))
    if outcome.rejected_by:
        print(_c(f"   rechazos: {', '.join(outcome.rejected_by)}", DIM))
    print()
    if outcome.output:
        print(outcome.output)
    return 0 if outcome.succeeded else 2


def main() -> int:
    parser = argparse.ArgumentParser(prog="localforge", description="Coding agent local")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("health", help="Verifica que el LLM local responde")

    ask = sub.add_parser("ask", help="Corre el agente sobre un repositorio")
    ask.add_argument("repo", help="Ruta del repositorio")
    ask.add_argument("objective", help="Que queres que haga el agente")
    ask.add_argument("-v", "--verbose", action="store_true", help="Muestra el texto del modelo")
    ask.add_argument("--max-turns", type=int, default=settings.max_turns)

    args = parser.parse_args()

    if args.command == "health":
        return asyncio.run(cmd_health())
    if args.command == "ask":
        return asyncio.run(
            cmd_ask(args.repo, args.objective, verbose=args.verbose, max_turns=args.max_turns)
        )
    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
