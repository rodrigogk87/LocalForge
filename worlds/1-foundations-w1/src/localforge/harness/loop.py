"""El agent loop.

Un agente es un while con presupuesto. Lo que separa un demo de un sistema no
es la sofisticacion del loop: es que SIEMPRE termina, y que cuando termina lo
hace en un estado que alguien puede leer.

Condiciones de terminacion implementadas:
  1. exito            -- el modelo dejo de pedir tools
  2. max_turns        -- limite logico de iteraciones
  3. wall_clock       -- limite temporal (el `for` solo acota iteraciones,
                         no duracion: un turno puede colgarse para siempre)
  4. token_budget     -- limite de gasto
  5. loop_detected    -- el modelo repite la misma accion sin progresar
"""

from __future__ import annotations

import asyncio
import json
import time
from collections import Counter
from pathlib import Path

from localforge.config import Settings, settings as default_settings
from localforge.harness.prompt import build_system_prompt
from localforge.models import (
    AgentMessage,
    AgentOutcome,
    AgentStatus,
    AgentTask,
    FailureReason,
    ModelResponse,
    StopReason,
    ToolCall,
    TurnRecord,
)
from localforge.providers.base import ModelProvider, ProviderError
from localforge.tools.base import ToolExecutor, ToolRegistry

# Repetir una accion una vez puede ser un reintento legitimo. Tres veces
# identicas significa que el modelo no esta incorporando el resultado.
REPEAT_LIMIT = 3


class AgentHarness:
    """El runtime que rodea al modelo.

    Hoy hace: contexto (trivial), tools, estado y terminacion.
    Todavia NO hace: verificacion, permisos, checkpoints. Esas son las fases
    siguientes y se enganchan en los bordes de este loop.
    """

    def __init__(
        self,
        provider: ModelProvider,
        registry: ToolRegistry,
        *,
        cfg: Settings | None = None,
        on_event: "EventSink | None" = None,
    ) -> None:
        self.provider = provider
        self.registry = registry
        self.cfg = cfg or default_settings
        self.on_event = on_event or (lambda *_args, **_kw: None)

    async def run(self, task: AgentTask) -> AgentOutcome:
        workspace = Path(task.repo_path).resolve()
        if not workspace.is_dir():
            return AgentOutcome(
                task_id=task.id,
                status=AgentStatus.FAILED,
                reason=FailureReason.PROVIDER_ERROR,
                output=f"el repositorio '{task.repo_path}' no existe o no es un directorio",
            )

        executor = ToolExecutor(
            self.registry,
            workspace,
            timeout_s=self.cfg.tool_timeout_s,
            output_limit=self.cfg.tool_output_limit,
        )
        system = build_system_prompt(workspace, self.registry)
        definitions = self.registry.definitions()

        # --- estado del loop -------------------------------------------------
        # Explicito y local a proposito: esta tupla es exactamente lo que en la
        # Fase 6 se serializa en un checkpoint. Lo que es facil de nombrar es
        # facil de persistir.
        messages: list[AgentMessage] = [AgentMessage(role="user", content=task.objective)]
        tokens_in = 0
        tokens_out = 0
        seen: Counter[str] = Counter()
        trajectory: list[str] = []
        records: list[TurnRecord] = []

        started = time.monotonic()
        deadline = started + task.wall_clock_s

        def finish(
            status: AgentStatus,
            *,
            reason: FailureReason | None = None,
            output: str = "",
            turns: int,
        ) -> AgentOutcome:
            return AgentOutcome(
                task_id=task.id,
                status=status,
                reason=reason,
                output=output,
                turns=turns,
                input_tokens=tokens_in,
                output_tokens=tokens_out,
                duration_ms=int((time.monotonic() - started) * 1000),
                trajectory=trajectory,
                turn_records=records,
            )

        for turn in range(task.max_turns):
            # Los presupuestos se chequean ANTES de gastar. Al reves ya pagaste
            # la llamada que sabias que no podias pagar.
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return finish(AgentStatus.FAILED, reason=FailureReason.WALL_CLOCK, turns=turn)
            if tokens_in + tokens_out > task.token_budget:
                return finish(AgentStatus.FAILED, reason=FailureReason.TOKEN_BUDGET, turns=turn)

            self.on_event("turn_start", turn=turn + 1, remaining_s=round(remaining, 1))

            try:
                # El timeout del turno nunca puede exceder lo que queda del
                # presupuesto total: es la composicion de budgets.
                response = await asyncio.wait_for(
                    self.provider.complete(messages, definitions, system=system),
                    timeout=min(self.cfg.request_timeout_s, remaining),
                )
            except asyncio.TimeoutError:
                return finish(AgentStatus.FAILED, reason=FailureReason.WALL_CLOCK, turns=turn + 1)
            except ProviderError as exc:
                return finish(
                    AgentStatus.FAILED,
                    reason=FailureReason.PROVIDER_ERROR,
                    output=str(exc),
                    turns=turn + 1,
                )

            tokens_in += response.input_tokens
            tokens_out += response.output_tokens
            self.on_event("model_response", turn=turn + 1, response=response)

            record = TurnRecord(
                turn=turn + 1,
                stop_reason=response.stop_reason,
                content=response.content,
                tool_calls=response.tool_calls,
                input_tokens=response.input_tokens,
                output_tokens=response.output_tokens,
                duration_ms=response.duration_ms,
            )

            # --- sin tool calls: el modelo termino de trabajar ---------------
            if not response.tool_calls:
                records.append(record)
                if response.stop_reason is StopReason.MAX_TOKENS:
                    # Una respuesta truncada NO es un resultado valido. Consume
                    # un turno del presupuesto, que es lo correcto.
                    messages.append(AgentMessage(role="assistant", content=response.content))
                    messages.append(
                        AgentMessage(role="user", content="Tu respuesta quedo cortada. Continua.")
                    )
                    continue
                return finish(
                    AgentStatus.COMPLETED, output=response.content or "", turns=turn + 1
                )

            # --- deteccion de loops -----------------------------------------
            for call in response.tool_calls:
                key = _signature(call)
                seen[key] += 1
                if seen[key] >= REPEAT_LIMIT:
                    records.append(record)
                    return finish(
                        AgentStatus.FAILED,
                        reason=FailureReason.LOOP_DETECTED,
                        output=f"el modelo repitio {call.name} con los mismos argumentos {REPEAT_LIMIT} veces",
                        turns=turn + 1,
                    )

            # --- ejecutar ----------------------------------------------------
            # El mensaje del assistant va ANTES de los resultados: primero queda
            # registrado que los pidio, despues que devolvieron.
            messages.append(
                AgentMessage(
                    role="assistant", content=response.content, tool_calls=response.tool_calls
                )
            )
            self.on_event("tools_start", turn=turn + 1, calls=response.tool_calls)
            results = await executor.run_all(response.tool_calls)

            # Correlacion por call_id, JAMAS por posicion.
            by_id = {r.call_id: r for r in results}
            missing = {c.id for c in response.tool_calls} - set(by_id)
            if missing:  # invariante del executor; si falla, es bug nuestro
                raise RuntimeError(f"faltan tool results para: {missing}")

            for call in response.tool_calls:
                result = by_id[call.id]
                trajectory.append(call.name)
                messages.append(
                    AgentMessage(
                        role="tool",
                        content=result.as_content(),
                        tool_call_id=call.id,
                        tool_name=call.name,
                    )
                )

            record.tool_results = results
            records.append(record)
            self.on_event("tools_done", turn=turn + 1, results=results)

        return finish(AgentStatus.FAILED, reason=FailureReason.MAX_TURNS, turns=task.max_turns)


def _signature(call: ToolCall) -> str:
    """Firma estable de una tool call.

    sort_keys para que el orden de las claves del dict no genere firmas
    distintas para la misma llamada.
    """
    return f"{call.name}:{json.dumps(call.arguments, sort_keys=True, default=str)}"


class EventSink:
    """Callable que recibe eventos del loop. Sirve para la CLI y el logging."""

    def __call__(self, event: str, **payload: object) -> None: ...


__all__ = ["AgentHarness", "ModelResponse", "REPEAT_LIMIT"]
