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

from localforge.config import settings
from localforge.harness import AgentHarness
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
            print(f"{stamp} {_c(f'── turno {payload['turn']}', BOLD)}")

        elif event == "model_response":
            response = payload["response"]
            assert isinstance(response, ModelResponse)
            meta = _c(
                f"{response.input_tokens}→{response.output_tokens} tok · {response.duration_ms / 1000:.1f}s",
                DIM,
            )
            print(f"{stamp}   modelo: {response.stop_reason.value} · {meta}")
            if response.content and self.verbose:
                head = response.content.strip().splitlines()[:3]
                for line in head:
                    print(f"{stamp}   {_c('│ ' + line[:110], DIM)}")

        elif event == "tools_start":
            calls = payload["calls"]
            assert isinstance(calls, list)
            for call in calls:
                assert isinstance(call, ToolCall)
                args = ", ".join(f"{k}={v!r}" for k, v in call.arguments.items())
                print(f"{stamp}   {_c('→', CYAN)} {call.name}({args[:100]})")

        elif event == "tools_done":
            results = payload["results"]
            assert isinstance(results, list)
            for result in results:
                assert isinstance(result, ToolResult)
                if result.success:
                    size = len(result.output or "")
                    mark = _c("✓", GREEN)
                    extra = _c(f"{size} chars{' · truncado' if result.truncated else ''}", DIM)
                    print(f"{stamp}   {mark} {result.name} {extra}")
                else:
                    print(f"{stamp}   {_c('✗', RED)} {result.name}: {result.error}")


# ---------------------------------------------------------------------------


async def cmd_health() -> int:
    provider = build_provider()
    try:
        info = await provider.health()
    except ProviderError as exc:
        print(_c(f"✗ {exc}", RED))
        return 1
    finally:
        await provider.aclose()

    print(_c("✓ inferencia local operativa", GREEN))
    for key, value in info.items():
        print(f"  {key:18} {value}")
    print(f"  {'num_ctx':18} {settings.num_ctx}")
    return 0


async def cmd_ask(repo: str, objective: str, *, verbose: bool, max_turns: int) -> int:
    repo_path = Path(repo).expanduser().resolve()
    if not repo_path.is_dir():
        print(_c(f"✗ '{repo}' no es un directorio", RED))
        return 1

    provider = build_provider()
    try:
        info = await provider.health()
    except ProviderError as exc:
        print(_c(f"✗ {exc}", RED))
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
    print(_c(f"── {outcome.summary()}", color))
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
