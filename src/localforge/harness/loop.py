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
  6. verification_failed -- el verifier rechazo y se agotaron las reparaciones
  7. context_overflow  -- no entra en la ventana ni compactando todo

Las dos ultimas son de fases posteriores y aparecen aca porque las condiciones
de terminacion son una sola lista: cada fase que agrega una capacidad agrega
tambien su forma de fallar. Un agente con mas features tiene mas maneras de
terminar, no menos.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections import Counter
from pathlib import Path

from localforge.config import Settings, settings as default_settings
from localforge.harness.checkpoint import Checkpoint, CheckpointStore
from localforge.harness.context import ContextBudget, ContextBuilder
from localforge.harness.prompt import build_system_prompt
from localforge.permissions import Approver, PermissionPolicy
from localforge.skills import discover_skills
from localforge.harness.state import StateMachine
from localforge.harness.verify import Verifier, default_verifier
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

# Cuanto se le permite pasarse a la ESTIMACION de contexto antes de abortar. El
# estimador tiene error conocido (ver harness/context.py); abortar una corrida
# sana por un 3% de error seria peor que el problema que evita.
_OVERFLOW_MARGIN = 1.10


class AgentHarness:
    """El runtime que rodea al modelo.

    Hoy hace: contexto (con presupuesto), tools, maquina de estados,
    verificacion con repair loop y terminacion.
    Todavia NO hace: permisos, sandbox, checkpoints. Esas son las fases
    siguientes y se enganchan en los bordes de este loop.
    """

    def __init__(
        self,
        provider: ModelProvider,
        registry: ToolRegistry,
        *,
        cfg: Settings | None = None,
        on_event: "EventSink | None" = None,
        context: ContextBuilder | None = None,
        verifier: Verifier | None = None,
        max_repairs: int = 2,
        policy: PermissionPolicy | None = None,
        approver: Approver | None = None,
        checkpoints: CheckpointStore | None = None,
    ) -> None:
        self.provider = provider
        self.registry = registry
        self.cfg = cfg or default_settings
        self.on_event = on_event or (lambda *_args, **_kw: None)
        # El builder es inyectable para poder testear presupuestos chicos sin
        # tocar la config global.
        self.context = context or ContextBuilder(ContextBudget.from_settings(self.cfg))
        # `verifier=None` usa el default; para desactivar la verificacion hay
        # que pasar uno que acepte todo. Es deliberado: apagar una garantia
        # tiene que ser explicito en el codigo que la apaga.
        self.verifier = verifier if verifier is not None else default_verifier()
        # Cuantas veces se le devuelve el rechazo al modelo antes de rendirse.
        # Sin techo, un modelo que no entiende el reproche gira hasta max_turns.
        self.max_repairs = max_repairs
        # El harness no decide permisos: los transporta hasta el executor, que
        # es quien puede verlos junto con los argumentos ya validados.
        self.policy = policy
        self.approver = approver
        # Sin store, el agente sigue funcionando y no sobrevive al proceso. La
        # persistencia es opt-in porque escribir en el disco del usuario no
        # deberia ser un efecto silencioso de correr el agente.
        self.checkpoints = checkpoints

    async def resume(self, task_id: str) -> AgentOutcome:
        """Retoma una corrida desde su ultimo checkpoint.

        No re-ejecuta nada: el checkpoint se graba DESPUES de aplicar los
        resultados de las tools al estado, asi que todo lo que esta guardado ya
        paso. Resumir es seguir, no repetir.
        """
        if self.checkpoints is None:
            raise RuntimeError("no hay checkpoint store configurado: no se puede resumir")
        snapshot = self.checkpoints.load(task_id)
        if snapshot is None:
            raise FileNotFoundError(f"no hay checkpoint para la task '{task_id}'")
        return await self.run(snapshot.task, resume_from=snapshot)

    async def run(self, task: AgentTask, *, resume_from: Checkpoint | None = None) -> AgentOutcome:
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
            policy=self.policy,
            approver=self.approver,
        )
        skills = discover_skills(workspace)
        system = build_system_prompt(workspace, self.registry, skills)
        if skills:
            self.on_event("skills_found", skills=[s.name for s in skills])
        definitions = self.registry.definitions()

        # --- estado del loop -------------------------------------------------
        # Explicito y local a proposito: esta tupla es exactamente lo que en la
        # Fase 6 se serializa en un checkpoint. Lo que es facil de nombrar es
        # facil de persistir.
        if resume_from is None:
            messages: list[AgentMessage] = [AgentMessage(role="user", content=task.objective)]
            tokens_in = 0
            tokens_out = 0
            seen: Counter[str] = Counter()
            trajectory: list[str] = []
            records: list[TurnRecord] = []
            machine = StateMachine()
            rejected_by: list[str] = []
            first_turn = 0
        else:
            # Restaurar es leer la misma tupla al reves. Que la lista de campos
            # de aca sea identica a la de arriba no es casualidad: es la prueba
            # de que el estado del loop esta completamente nombrado.
            messages = list(resume_from.messages)
            tokens_in = resume_from.tokens_in
            tokens_out = resume_from.tokens_out
            seen = Counter(resume_from.seen)
            trajectory = list(resume_from.trajectory)
            records = list(resume_from.records)
            machine = StateMachine(status=resume_from.status)
            rejected_by = list(resume_from.rejected_by)
            first_turn = resume_from.turn
            if resume_from.chars_per_token:
                self.context.estimator.chars_per_token = resume_from.chars_per_token
            self.on_event("resumed", turn=first_turn, task_id=str(task.id))

        started = time.monotonic()
        deadline = started + task.wall_clock_s

        def finish(
            status: AgentStatus,
            *,
            reason: FailureReason | None = None,
            output: str = "",
            turns: int,
        ) -> AgentOutcome:
            # La maquina se mueve al estado terminal aca y en ningun otro lado,
            # asi el camino registrado siempre termina donde termino la corrida.
            if machine.status is not status and not machine.status.is_terminal:
                machine.to(status, turn=turns, note=reason.value if reason else "")
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
                state_path=machine.compact_path(),
                repairs=machine.repairs,
                rejected_by=rejected_by,
            )

        if machine.status is AgentStatus.CREATED:
            machine.to(AgentStatus.RUNNING, note="arranca la primera vuelta")

        def snapshot(turn: int) -> None:
            """Graba el estado si hay donde. Se llama al CERRAR cada turno.

            Al cerrar y no al abrir: un checkpoint tomado antes de aplicar los
            resultados de las tools obligaria a re-ejecutarlas al resumir, y
            entonces resumir no seria idempotente.
            """
            if self.checkpoints is None:
                return
            self.checkpoints.save(
                Checkpoint(
                    task=task,
                    turn=turn,
                    status=machine.status,
                    messages=messages,
                    tokens_in=tokens_in,
                    tokens_out=tokens_out,
                    seen=dict(seen),
                    trajectory=trajectory,
                    records=records,
                    state_path=machine.compact_path(),
                    repairs=machine.repairs,
                    rejected_by=rejected_by,
                    chars_per_token=self.context.estimator.chars_per_token,
                )
            )

        for turn in range(first_turn, task.max_turns):
            # Los presupuestos se chequean ANTES de gastar. Al reves ya pagaste
            # la llamada que sabias que no podias pagar.
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return finish(AgentStatus.FAILED, reason=FailureReason.WALL_CLOCK, turns=turn)
            if tokens_in + tokens_out > task.token_budget:
                return finish(AgentStatus.FAILED, reason=FailureReason.TOKEN_BUDGET, turns=turn)

            self.on_event("turn_start", turn=turn + 1, remaining_s=round(remaining, 1))

            # El contexto NO es la lista de mensajes: es una proyeccion de esa
            # lista que entra en el presupuesto. El loop sigue siendo dueño del
            # state completo (`messages`); lo que viaja es `built.messages`.
            built = self.context.build(
                system=system,
                task=task.objective,
                messages=messages,
                definitions=definitions,
            )
            self.on_event("context_built", turn=turn + 1, breakdown=built.breakdown)

            # Si no entra ni despues de compactar todo lo compactable, cortar
            # limpio. La alternativa es mandarlo igual y dejar que Ollama
            # trunque en silencio por la izquierda, comiendose el system prompt:
            # el agente sigue "funcionando" sin sus instrucciones.
            # El margen existe porque `total` es una ESTIMACION; sin el, un
            # estimador todavia sin calibrar podria abortar una corrida sana.
            if built.breakdown.total > built.breakdown.available * _OVERFLOW_MARGIN:
                return finish(
                    AgentStatus.FAILED,
                    reason=FailureReason.CONTEXT_OVERFLOW,
                    output=(
                        f"el contexto no entra: ~{built.breakdown.total} tokens estimados contra "
                        f"{built.breakdown.available} disponibles, ya compactado. "
                        f"Subí LOCALFORGE_NUM_CTX o acotá la tarea."
                    ),
                    turns=turn + 1,
                )

            try:
                # El timeout del turno nunca puede exceder lo que queda del
                # presupuesto total: es la composicion de budgets.
                response = await asyncio.wait_for(
                    self.provider.complete(built.messages, definitions, system=built.system),
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
            # `prompt_eval_count` es el conteo REAL del tokenizer del modelo.
            # Se lo devolvemos al estimador para que deje de ser una heuristica.
            self.context.observe_actual(built.estimated_input, response.input_tokens)
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
                # --- verificacion -------------------------------------------
                # El modelo dejo de pedir tools, pero "termine" no es su
                # decision: es la del harness, que puede mirar lo que HIZO.
                answer = response.content or ""
                machine.to(AgentStatus.VERIFYING, turn=turn + 1)
                verdict = self.verifier.verify(task, answer, trajectory)
                self.on_event("verified", turn=turn + 1, verdict=verdict)

                if verdict.ok:
                    return finish(AgentStatus.COMPLETED, output=answer, turns=turn + 1)

                rejected_by.append(verdict.check)
                if machine.repairs >= self.max_repairs:
                    # Se agotaron las reparaciones. Devolvemos igual la ultima
                    # respuesta: es mala, pero el usuario la prefiere a nada, y
                    # el `reason` deja claro que no paso la verificacion.
                    return finish(
                        AgentStatus.FAILED,
                        reason=FailureReason.VERIFICATION_FAILED,
                        output=answer,
                        turns=turn + 1,
                    )

                machine.to(AgentStatus.REPAIRING, turn=turn + 1, note=verdict.check)
                messages.append(AgentMessage(role="assistant", content=answer))
                messages.append(AgentMessage(role="user", content=verdict.feedback))
                machine.to(AgentStatus.RUNNING, turn=turn + 1)
                snapshot(turn + 1)
                continue

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
            machine.to(AgentStatus.WAITING_TOOL, turn=turn + 1)
            results = await executor.run_all(response.tool_calls)
            machine.to(AgentStatus.RUNNING, turn=turn + 1)

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
            snapshot(turn + 1)

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
