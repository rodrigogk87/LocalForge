"""Presentacion en consola: colores, marcas y el loop en vivo.

Un agente que tarda un minuto sin decir nada es indistinguible de uno colgado.
Este modulo es la observabilidad minima, y vive aparte de los comandos porque son
dos cosas distintas: COMO se muestra algo y QUE se hace.
"""

from __future__ import annotations

import asyncio
import sys
import time

from localforge.context import ContextBreakdown
from localforge.models import ModelResponse, ToolCall, ToolResult
from localforge.sandbox import Approver, DenyingApprover

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


def c(text: str, color: str) -> str:
    return f"{color}{text}{OFF}" if _supports_color() else text


class ConsoleSink:
    """Muestra el loop mientras corre.

    Un agente que tarda un minuto sin decir nada es indistinguible de uno
    colgado. Ver las tool calls en vivo es la observabilidad minima.
    """

    def __init__(self, verbose: bool = False) -> None:
        self.verbose = verbose
        self._t0 = time.monotonic()

    def __call__(self, event: str, **payload: object) -> None:
        stamp = c(f"[{time.monotonic() - self._t0:6.1f}s]", DIM)

        if event == "resumed":
            print(c(f"{stamp} retomando desde el turno {payload['turn']}", YELLOW))

        elif event == "turn_start":
            print(f"{stamp} {c(RULE + ' turno ' + str(payload['turn']), BOLD)}")

        elif event == "context_built":
            bd = payload["breakdown"]
            assert isinstance(bd, ContextBreakdown)
            # Sin verbose, una linea: lo que se gasto y si hubo que compactar.
            warn = bd.pct >= 75
            line = f"{bd.total}/{bd.available} tok ({bd.pct:.0f}%)"
            if bd.compacted_messages:
                line += f" · compactado {bd.compacted_messages} obs (-{bd.recovered_tokens} tok)"
            print(f"{stamp}   ctx: {c(line, YELLOW if warn else DIM)}")
            if self.verbose:
                for row in bd.table().splitlines()[1:]:
                    print(f"{stamp} {c(row, DIM)}")

        elif event == "model_response":
            response = payload["response"]
            assert isinstance(response, ModelResponse)
            meta = c(
                f"{response.input_tokens}{ARROW}{response.output_tokens} tok | {response.duration_ms / 1000:.1f}s",
                DIM,
            )
            print(f"{stamp}   modelo: {response.stop_reason.value} · {meta}")
            if response.content and self.verbose:
                head = response.content.strip().splitlines()[:3]
                for line in head:
                    print(f"{stamp}   {c(PIPE + ' ' + line[:110], DIM)}")

        elif event == "verified":
            verdict = payload["verdict"]
            if verdict.ok:
                print(f"{stamp}   {c(OK_MARK, GREEN)} verificado ({verdict.check})")
            else:
                print(f"{stamp}   {c(BAD_MARK + ' rechazado por ' + verdict.check, YELLOW)}")
                if self.verbose:
                    print(f"{stamp}   {c(PIPE + ' ' + verdict.feedback[:150], DIM)}")

        elif event == "tools_start":
            calls = payload["calls"]
            assert isinstance(calls, list)
            for call in calls:
                assert isinstance(call, ToolCall)
                args = ", ".join(f"{k}={v!r}" for k, v in call.arguments.items())
                print(f"{stamp}   {c(ARROW, CYAN)} {call.name}({args[:100]})")

        elif event == "tools_done":
            results = payload["results"]
            assert isinstance(results, list)
            for result in results:
                assert isinstance(result, ToolResult)
                if result.success:
                    size = len(result.output or "")
                    mark = c(OK_MARK, GREEN)
                    extra = c(f"{size} chars{' · truncado' if result.truncated else ''}", DIM)
                    print(f"{stamp}   {mark} {result.name} {extra}")
                else:
                    print(f"{stamp}   {c(BAD_MARK, RED)} {result.name}: {result.error}")



class ConsoleApprover:
    """Aprobacion humana por consola.

    Muestra la tool, sus argumentos y el motivo, y espera un si explicito.
    Cualquier cosa que no sea "s" o "y" es un no: un enter distraido no puede
    autorizar una escritura.
    """

    def __init__(self) -> None:
        self.recordar: dict[str, bool] = {}

    async def approve(self, call: ToolCall, reason: str) -> bool:
        if call.name in self.recordar:
            return self.recordar[call.name]

        args = ", ".join(f"{k}={v!r}" for k, v in call.arguments.items())
        print()
        print(c(f"  {BAD_MARK} el agente pide permiso", YELLOW))
        print(f"    tool   {call.name}({args[:160]})")
        print(f"    motivo {reason}")
        try:
            # asyncio.to_thread para no bloquear el event loop mientras el
            # humano piensa: si hay tools corriendo en paralelo, siguen.
            respuesta = (
                await asyncio.to_thread(input, "    ¿permitir? [s/N/t=siempre esta tool] ")
            ).strip().lower()
        except (EOFError, KeyboardInterrupt):
            print(c("    sin respuesta -> denegado", DIM))
            return False

        if respuesta == "t":
            self.recordar[call.name] = True
            return True
        ok = respuesta in ("s", "si", "y", "yes")
        print(c(f"    -> {'permitido' if ok else 'denegado'}", GREEN if ok else RED))
        return ok


def build_approver(read_only: bool) -> Approver:
    """Quien resuelve los ASK.

    Sin TTY no hay humano, y un ASK que nadie puede contestar es un DENY. Que la
    eleccion dependa del entorno y no de un flag es deliberado: en CI, donde mas
    importa, no hay forma de olvidarse de pasar el flag seguro.
    """
    if read_only or not sys.stdin.isatty():
        return DenyingApprover()
    return ConsoleApprover()


__all__ = [
    "ConsoleSink", "ConsoleApprover", "build_approver",
    "DIM", "BOLD", "CYAN", "GREEN", "RED", "YELLOW", "OFF",
    "OK_MARK", "BAD_MARK", "ARROW", "RULE", "PIPE", "c",
]
