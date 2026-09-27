"""Interface de tools, registry y ejecucion.

El modelo NUNCA ejecuta nada: propone. El harness resuelve, valida, **autoriza**,
ejecuta y reporta. Esa asimetria es el punto donde se enchufan los permisos y la
aprobacion humana (Fase 5) sin tocar el loop -- y efectivamente se enchufaron
aca, en `run_one`, sin que el loop se enterara.

Falta el sandbox: un permiso decide *si* se ejecuta, un sandbox contiene *lo que
pasa* cuando se ejecuta.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ValidationError

from localforge.sandbox import (
    Approver,
    Decision,
    DenyingApprover,
    PermissionPolicy,
    Verdict,
    default_policy,
)
from localforge.models import ToolCall, ToolDefinition, ToolResult


class ToolError(Exception):
    """Fallo esperable de una tool.

    Se convierte en ToolResult(success=False) y vuelve al modelo como
    feedback. El mensaje es una API dirigida al modelo: decile que salio mal
    Y que hacer al respecto.
    """


@runtime_checkable
class Tool(Protocol):
    name: str
    description: str
    args_model: type[BaseModel]

    async def run(self, workspace: Path, args: BaseModel) -> str: ...


class ToolRegistry:
    """Catalogo de lo que el modelo puede pedir.

    Una tool que no esta en el registry no existe para el modelo: es el primer
    control de permisos, anterior a cualquier sandbox.
    """

    def __init__(self, tools: list[Tool] | None = None) -> None:
        self._tools: dict[str, Tool] = {}
        for tool in tools or []:
            self.register(tool)

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"tool duplicada: {tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return sorted(self._tools)

    def definitions(self) -> list[ToolDefinition]:
        """Lo que se le manda al modelo en cada turno.

        El schema sale de `model_json_schema()` del modelo de argumentos: una
        definicion produce la descripcion que lee el modelo y el validador que
        parsea su respuesta. Sin dos fuentes que puedan divergir.
        """
        out: list[ToolDefinition] = []
        for name in self.names():
            tool = self._tools[name]
            schema = tool.args_model.model_json_schema()
            schema.pop("title", None)
            out.append(
                ToolDefinition(name=name, description=tool.description.strip(), input_schema=schema)
            )
        return out


class ToolExecutor:
    """Resuelve, valida, ejecuta y correlaciona."""

    def __init__(
        self,
        registry: ToolRegistry,
        workspace: Path,
        *,
        timeout_s: float = 30.0,
        output_limit: int = 8_000,
        max_parallel: int = 4,
        policy: PermissionPolicy | None = None,
        approver: Approver | None = None,
    ) -> None:
        self.registry = registry
        self.workspace = workspace
        self.timeout_s = timeout_s
        self.output_limit = output_limit
        self._sem = asyncio.Semaphore(max_parallel)
        # Sin politica explicita se usa la default, que es restrictiva. Pasar
        # `policy=None` no significa "sin permisos": no hay forma de apagar el
        # control desde los argumentos, hay que construir una politica permisiva
        # a mano y que se vea en el codigo.
        if policy is None:
            policy = default_policy()
        self.policy = policy
        self.approver = approver or DenyingApprover()

    async def run_one(self, call: ToolCall) -> ToolResult:
        started = time.monotonic()

        def elapsed() -> int:
            return int((time.monotonic() - started) * 1000)

        tool = self.registry.get(call.name)
        if tool is None:
            # El modelo alucina nombres de tools: es esperable, no excepcional.
            # Decirle cuales SI existen sube muchisimo la tasa de correccion.
            return ToolResult(
                call_id=call.id,
                name=call.name,
                success=False,
                error=(
                    f"tool desconocida '{call.name}'. "
                    f"Disponibles: {', '.join(self.registry.names())}"
                ),
                duration_ms=elapsed(),
            )

        try:
            args = tool.args_model.model_validate(call.arguments)
        except ValidationError as exc:
            # .errors() dice que campo exacto esta mal; str(exc) es mas ruidoso
            # y menos accionable para el modelo.
            detail = "; ".join(
                f"{'.'.join(str(p) for p in e['loc']) or '(raiz)'}: {e['msg']}" for e in exc.errors()
            )
            return ToolResult(
                call_id=call.id,
                name=call.name,
                success=False,
                error=f"argumentos invalidos para {call.name}: {detail}",
                duration_ms=elapsed(),
            )

        # --- autorizacion -------------------------------------------------
        # Va DESPUES de validar los argumentos, porque la decision puede depender
        # de ellos (leer `src/main.py` y leer `.env` son la misma tool), y ANTES
        # de ejecutar, que es lo unico que importa.
        verdict = self.policy.decide(call)
        if verdict.decision is Decision.ASK:
            approved = await self.approver.approve(call, verdict.reason)
            verdict = _resolve_ask(verdict, approved)

        if verdict.decision is not Decision.ALLOW:
            # Un permiso denegado vuelve como feedback, no como excepcion: el
            # modelo lee el motivo y busca otro camino.
            return ToolResult(
                call_id=call.id,
                name=call.name,
                success=False,
                error=f"permiso denegado para {call.name}: {verdict.reason}",
                duration_ms=elapsed(),
            )

        async with self._sem:
            try:
                raw = await asyncio.wait_for(tool.run(self.workspace, args), timeout=self.timeout_s)
            except asyncio.TimeoutError:
                return ToolResult(
                    call_id=call.id,
                    name=call.name,
                    success=False,
                    error=f"timeout: la tool supero {self.timeout_s}s",
                    duration_ms=elapsed(),
                )
            except ToolError as exc:
                return ToolResult(
                    call_id=call.id, name=call.name, success=False, error=str(exc), duration_ms=elapsed()
                )
            except Exception as exc:  # noqa: BLE001 - aislar fallos de una tool
                # Un fallo inesperado de una tool no debe matar al agente.
                # `except Exception` no atrapa CancelledError (BaseException),
                # asi que los timeouts externos siguen propagandose.
                return ToolResult(
                    call_id=call.id,
                    name=call.name,
                    success=False,
                    error=f"{type(exc).__name__}: {exc}",
                    duration_ms=elapsed(),
                )

        output, truncated = self._truncate(raw)
        return ToolResult(
            call_id=call.id,
            name=call.name,
            success=True,
            output=output,
            duration_ms=elapsed(),
            truncated=truncated,
        )

    async def run_all(self, calls: list[ToolCall]) -> list[ToolResult]:
        """Todas las tools del turno en paralelo.

        `run_one` atrapa todos los fallos NORMALES de una tool y siempre
        devuelve un ToolResult, asi que en operacion normal no hay excepciones
        que escapen de gather.

        Lo que si escapa es BaseException, CancelledError incluido -- y es
        deliberado: es asi como el wall clock del harness corta el turno entero.
        """
        return list(await asyncio.gather(*(self.run_one(c) for c in calls)))

    def _truncate(self, text: str) -> tuple[str, bool]:
        """Nunca truncar en silencio.

        Si el modelo cree que vio el archivo entero cuando no fue asi, razona
        sobre informacion faltante sin ninguna senal. Se le dice que se corto
        y como pedir el resto.
        """
        if len(text) <= self.output_limit:
            return text, False
        head = text[: self.output_limit]
        return (
            head
            + f"\n\n[...truncado: se muestran {self.output_limit} de {len(text)} caracteres. "
            "Usa los parametros offset/limit de la tool para leer el resto.]",
            True,
        )


def _resolve_ask(verdict, approved: bool):  # noqa: ANN001, ANN202
    """Convierte un ASK resuelto en ALLOW o DENY.

    Aparte a proposito: es el unico lugar del codigo donde un ASK se transforma,
    y quien lea el permiso sabe que un ASK nunca queda sin resolver.
    """
    if approved:
        return Verdict(Decision.ALLOW, "aprobado por el usuario")
    return Verdict(
        Decision.DENY,
        (verdict.reason + " — " if verdict.reason else "")
        + "no fue aprobado (en modo no interactivo un ASK se resuelve como DENY)",
    )
