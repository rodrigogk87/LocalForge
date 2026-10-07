"""Modelo de datos de LocalForge.

Todo lo que cruza un borde de confianza pasa por estas clases: el harness contra
el modelo, el harness contra las tools, y el proceso de hoy contra el checkpoint
que lea el de manana.

**Este es el unico archivo que TODOS los mundos tocan.** Cada fase que agrega una
capacidad agrega tambien su vocabulario: los estados de verificacion salieron del
Mundo 3, `CONTEXT_OVERFLOW` del Mundo 2, los campos de reparacion del outcome del
Mundo 3. Por eso cada valor lleva marcado de donde vino -- leerlo de arriba a
abajo es leer la historia del proyecto.

Que eso sea una linea por valor y no una migracion es la razon de que estos tipos
sean enums cerrados y modelos explicitos, en vez de strings y diccionarios.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Estado
# ---------------------------------------------------------------------------


class AgentStatus(StrEnum):
    """Ciclo de vida de una task.

    Las transiciones permitidas entre estos valores viven en
    `harness/state.py`: aca solo estan los nombres. Un enum no puede expresar
    "de VERIFYING no se puede volver a WAITING_TOOL", y esa es justamente la
    parte que hace que sea una maquina de estados y no una etiqueta.

    PLANNING llego con el planner, en el paso 9, y no antes: mientras no habia
    planner, un estado por el que el agente pasaba sin hacer nada habria mentido
    sobre lo que el sistema hace.

    Los cinco primeros valores son del Mundo 1; los tres de verificacion se
    agregaron en el Mundo 3. Un enum no se puede partir en dos archivos, asi que
    los nombres viven juntos aca aunque pertenezcan a fases distintas.
    """

    # --- Mundo 1: lo minimo para que una task tenga ciclo de vida ----------
    CREATED = "created"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

    # --- Mundo 3: los estados que aparecen al meter verificacion -----------
    # El agente pidio tools y espera resultados. Se distingue de RUNNING porque
    # es el unico momento en que corre codigo que no es del harness.
    WAITING_TOOL = "waiting_tool"
    # Hay una respuesta candidata y el verifier la esta juzgando. Antes del
    # Mundo 3 este estado no existia porque nadie juzgaba nada: el modelo dejaba
    # de pedir tools y su respuesta se aceptaba.
    VERIFYING = "verifying"
    # El verifier rechazo y el agente tiene otra oportunidad, con feedback.
    REPAIRING = "repairing"

    # --- Mundo 3, completado: el planner ----------------------------------
    # El modelo escribe un plan ANTES de tocar el repo. Solo se pasa por aca si
    # la corrida tiene planner: el estado existe porque algo pasa en el.
    PLANNING = "planning"

    @property
    def is_terminal(self) -> bool:
        return self in (AgentStatus.COMPLETED, AgentStatus.FAILED, AgentStatus.CANCELLED)


class StopReason(StrEnum):
    """Por que el modelo dejo de generar. Es la senal de control del loop.

    El unico de los tres enums que no cambio desde el Mundo 1: describe lo que
    hace el MODELO, y eso no depende de las capacidades que le agreguemos al
    harness alrededor.
    """

    END_TURN = "end_turn"
    TOOL_USE = "tool_use"
    MAX_TOKENS = "max_tokens"
    UNKNOWN = "unknown"


class FailureReason(StrEnum):
    """Vocabulario cerrado de motivos de corte.

    Cerrado a proposito: en el Mundo 7 estos valores se agrupan para armar la
    taxonomia de fallos. Un string libre distinto en cada rama es inagrupable.

    **Arranco con seis valores y hoy tiene ocho, y ese crecimiento es el argumento
    a favor del enum.** Agregar un motivo nuevo fue una linea; la taxonomia del
    Mundo 7 lo agrupa sin tocar nada, porque cuenta sobre un campo que ya existia.
    Con strings libres, cada motivo nuevo habria sido un string mas que nadie sabe
    agrupar.
    """

    # --- Mundo 1: se acabo el presupuesto, o el proveedor fallo ------------
    MAX_TURNS = "max_turns"
    WALL_CLOCK = "wall_clock"
    TOKEN_BUDGET = "token_budget"
    LOOP_DETECTED = "loop_detected"
    PROVIDER_ERROR = "provider_error"
    CANCELLED = "cancelled"

    # --- Mundo 2: el contexto no entra ni compactando lo compactable -------
    # Cortar aca es mejor que mandarlo igual y dejar que Ollama trunque en
    # silencio por la izquierda, comiendose el system prompt.
    CONTEXT_OVERFLOW = "context_overflow"

    # --- Mundo 3: el agente termino, pero el resultado no paso ------------
    # Se distingue de todos los de arriba porque el agente TERMINO de trabajar:
    # el problema es la calidad del resultado, no la ejecucion. El Mundo 7 usa
    # justo esa diferencia para separar fallos de ejecucion de fallos de calidad.
    VERIFICATION_FAILED = "verification_failed"


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


class ToolDefinition(BaseModel):
    """Lo que el modelo ve de una tool.

    `input_schema` es JSON Schema serializado (no un modelo Pydantic) porque
    tiene que viajar por la red hasta el proveedor. Se genera desde el modelo
    de argumentos con `model_json_schema()`: una definicion, dos usos.
    """

    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    input_schema: dict[str, Any] = Field(default_factory=dict)

    def to_ollama(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_schema,
            },
        }


class ToolCall(BaseModel):
    """La peticion del modelo para usar una tool.

    `id` es la clave de correlacion con su ToolResult. Nunca se correlaciona
    por posicion en la lista: con ejecucion paralela el orden de finalizacion
    no es el de pedido.
    """

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)


class ToolResult(BaseModel):
    """Resultado de ejecutar una tool, exitoso o no.

    `success` no tiene default a proposito: quien construye el resultado esta
    obligado a decidir. Un default optimista convierte cada olvido en un
    falso positivo que el modelo lee como "salio bien".
    """

    call_id: str = Field(min_length=1)
    name: str = ""
    success: bool
    output: str | None = None
    error: str | None = None
    duration_ms: int = 0
    truncated: bool = False

    @model_validator(mode="after")
    def _coherent(self) -> ToolResult:
        if self.success and self.error is not None:
            raise ValueError("un resultado exitoso no puede traer error")
        if not self.success and not self.error:
            raise ValueError("un fallo debe explicar por que fallo")
        return self

    def as_content(self) -> str:
        """Lo que efectivamente vuelve al contexto del modelo."""
        if self.success:
            return self.output or "(sin salida)"
        # El prefijo es deliberado: los modelos responden mejor a errores
        # marcados lexicamente de forma explicita.
        return f"ERROR: {self.error}"


# ---------------------------------------------------------------------------
# Conversacion
# ---------------------------------------------------------------------------

Role = Literal["system", "user", "assistant", "tool"]


class AgentMessage(BaseModel):
    role: Role
    content: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    # Solo los mensajes de rol "tool" referencian una tool call previa.
    tool_call_id: str | None = None
    tool_name: str | None = None
    created_at: datetime = Field(default_factory=_utcnow)

    model_config = ConfigDict(use_enum_values=True)

    def to_ollama(self) -> dict[str, Any]:
        """Traduce al formato de /api/chat.

        Ollama no acepta `tool_call_id`: correlaciona el resultado con la tool
        por `tool_name` y por posicion en la conversacion. Nosotros igual
        guardamos el call_id del lado nuestro, que es la correlacion real.
        """
        msg: dict[str, Any] = {"role": self.role, "content": self.content or ""}
        if self.tool_calls:
            msg["tool_calls"] = [
                {"function": {"name": c.name, "arguments": c.arguments}} for c in self.tool_calls
            ]
        if self.role == "tool" and self.tool_name:
            msg["tool_name"] = self.tool_name
        return msg


# ---------------------------------------------------------------------------
# Respuesta del modelo
# ---------------------------------------------------------------------------


class ModelResponse(BaseModel):
    content: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    stop_reason: StopReason = StopReason.UNKNOWN
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = ""
    duration_ms: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


# ---------------------------------------------------------------------------
# Task y outcome
# ---------------------------------------------------------------------------


class AgentTask(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    objective: str = Field(min_length=1, description="Lo que el usuario quiere que el agente logre")
    repo_path: str = Field(min_length=1, description="Raiz del repositorio sobre el que trabaja")
    status: AgentStatus = AgentStatus.CREATED

    max_turns: int = Field(default=20, ge=1, le=100)
    wall_clock_s: float = Field(default=300.0, gt=0)
    token_budget: int = Field(default=200_000, gt=0)

    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_utcnow)


class TurnRecord(BaseModel):
    """Una vuelta del loop. Es la unidad de la trayectoria."""

    turn: int
    stop_reason: StopReason
    content: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    tool_results: list[ToolResult] = Field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    duration_ms: int = 0


class AgentOutcome(BaseModel):
    """El registro que hace evaluable al agente.

    Devolver un string suelto no permite distinguir "termino bien" de "se
    acabaron los turnos", ni medir costo, ni comparar dos versiones del
    harness. Esto es el input de las evals de la Fase 7.
    """

    task_id: UUID
    status: AgentStatus
    reason: FailureReason | None = None
    output: str = ""

    turns: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    duration_ms: int = 0

    trajectory: list[str] = Field(default_factory=list)
    turn_records: list[TurnRecord] = Field(default_factory=list)

    # Fase 3. El camino de estados y cuantas veces el verifier rechazo son
    # señales de eval: distinguen "salio bien de una" de "salio bien al tercer
    # intento", que para medir un harness no es lo mismo.
    state_path: str = ""
    repairs: int = 0
    rejected_by: list[str] = Field(default_factory=list)

    # El plan con el que arranco, si hubo planner. Vacio no es "no pudo
    # planear": es "nadie le pidio un plan".
    plan: list[str] = Field(default_factory=list)

    @property
    def succeeded(self) -> bool:
        return self.status == AgentStatus.COMPLETED

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def summary(self) -> str:
        head = f"{self.status.value}"
        if self.reason:
            head += f" ({self.reason.value})"
        if self.repairs:
            head += f" | {self.repairs} reparacion(es)"
        return (
            f"{head} | {self.turns} turnos | {self.total_tokens} tokens "
            f"| {self.duration_ms / 1000:.1f}s | tools: {', '.join(self.trajectory) or '-'}"
        )
