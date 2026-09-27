"""El presupuesto y quien lo aplica.

Tercera y cuarta pieza del Mundo 2. La idea central, y la razon de que esto sea
un modulo y no tres ifs dentro del loop: **el contexto no se acumula, se ASIGNA
en cada turno**. El loop no decide que entra; le pide al builder que lo decida y
le reporte que hizo.

Dos cosas que este paquete NO hace todavia, a proposito:

- **Retrieval just-in-time** (W2-C11): traer el fragmento exacto que hace falta
  en vez de archivos enteros. Hoy `search_code` existe, asi que esto ya no esta
  bloqueado -- solo no esta hecho.
- **Aislamiento de contexto** (W2-C13): darle a cada subagente su propia
  ventana. Eso vive en `agents/subagent.py`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from localforge.context.layers import LAYER_ORDER, ContextBreakdown, Layer
from localforge.context.tokens import TokenEstimator
from localforge.models import AgentMessage, ToolDefinition


@dataclass(frozen=True)
class ContextBudget:
    """Como se reparte la ventana.

    `limit` no se puede gastar entero en input: el modelo tiene que poder
    ESCRIBIR la respuesta dentro de la misma ventana. Reservar lugar para la
    salida no es opcional, es parte del presupuesto -- y es el error que hace
    que un agente funcione en pruebas cortas y se rompa en tareas largas.
    """

    limit: int
    # Lugar para la respuesta del modelo. Un turno con tool calls es corto;
    # el ultimo, el del entregable, puede ser largo.
    reserve_output: int = 4_000
    # Piso de conversacion que no se compacta nunca. Sin esto, con un contexto
    # muy chico el compactador se comeria hasta el ultimo turno y el modelo
    # perderia el resultado que acaba de pedir.
    keep_recent_messages: int = 4

    @property
    def available(self) -> int:
        return max(0, self.limit - self.reserve_output)

    @classmethod
    def from_settings(cls, cfg) -> ContextBudget:  # noqa: ANN001 - Settings, evita import ciclico
        return cls(limit=cfg.num_ctx)


_COMPACTED_TEMPLATE = (
    "[observacion compactada: {name} habia devuelto {chars} caracteres que ya no "
    "estan en el contexto. Si los necesitas, volve a pedir la tool.]"
)


@dataclass
class BuiltContext:
    """Lo que el loop le manda al provider este turno, mas la cuenta de lo que hizo."""

    system: str
    messages: list[AgentMessage]
    breakdown: ContextBreakdown
    estimated_input: int = field(default=0)

    def __post_init__(self) -> None:
        self.estimated_input = self.breakdown.total


class ContextBuilder:
    """Decide que se le manda al modelo en cada turno.

    No guarda la conversacion: el loop sigue siendo el dueño del state. El
    builder recibe el state completo y devuelve una PROYECCION de ese state que
    entra en el presupuesto. Esa distincion (state != context) es el corazon de
    W2-C8 y lo que permite que la Fase 6 serialice el state entero sin que el
    contexto crezca con el.
    """

    def __init__(self, budget: ContextBudget, estimator: TokenEstimator | None = None) -> None:
        self.budget = budget
        self.estimator = estimator or TokenEstimator()

    # -- API ---------------------------------------------------------------

    def build(
        self,
        *,
        system: str,
        task: str,
        messages: Sequence[AgentMessage],
        definitions: Sequence[ToolDefinition],
    ) -> BuiltContext:
        est = self.estimator

        fixed = (
            est.estimate(system)
            + est.estimate_tools(definitions)
            + est.estimate(task)
        )
        room = self.budget.available - fixed

        projected, compacted, recovered = self._compact(list(messages), room)
        breakdown = self._breakdown(
            system=system,
            task=task,
            messages=projected,
            definitions=definitions,
            compacted=compacted,
            recovered=recovered,
        )
        return BuiltContext(system=system, messages=projected, breakdown=breakdown)

    def observe_actual(self, estimated: int, actual: int) -> None:
        """Le pasa al estimador el conteo real del provider, para calibrar."""
        self.estimator.observe(estimated, actual)

    # -- compactacion ------------------------------------------------------

    def _compact(
        self, messages: list[AgentMessage], room: int
    ) -> tuple[list[AgentMessage], int, int]:
        """Compacta observaciones viejas hasta que la conversacion entre en `room`.

        Estrategia, y por que esta:

        - Se compactan **observaciones** (mensajes de rol "tool"), que son la
          capa mas grande y la menos reutilizable: el modelo ya extrajo lo que
          necesitaba de ese archivo en el turno siguiente.
        - Se compactan **de la mas vieja a la mas nueva**. Lo reciente es lo que
          el modelo esta usando ahora.
        - Nunca se toca el mensaje de la task, ni el razonamiento del assistant,
          ni los ultimos `keep_recent_messages`.
        - **Nunca en silencio.** Cada observacion compactada deja un marcador
          que dice que habia ahi y como recuperarlo. Es la misma regla que
          `ToolExecutor._truncate` de la Fase 1: si el modelo cree que vio algo
          que no vio, razona sobre informacion faltante sin ninguna señal.
        """
        est = self.estimator
        if est.estimate_messages(messages) <= room:
            return messages, 0, 0

        out = list(messages)
        protected_from = max(0, len(out) - self.budget.keep_recent_messages)
        compacted = 0
        recovered = 0

        for i in range(protected_from):
            if est.estimate_messages(out) <= room:
                break
            msg = out[i]
            if msg.role != "tool" or not msg.content:
                continue
            if msg.content.startswith("[observacion compactada"):
                continue  # ya compactada en un turno anterior
            before = est.estimate(msg.content)
            marker = _COMPACTED_TEMPLATE.format(
                name=msg.tool_name or "una tool", chars=len(msg.content)
            )
            out[i] = msg.model_copy(update={"content": marker})
            recovered += before - est.estimate(marker)
            compacted += 1

        return out, compacted, max(0, recovered)

    # -- anatomia ----------------------------------------------------------

    def _breakdown(
        self,
        *,
        system: str,
        task: str,
        messages: Sequence[AgentMessage],
        definitions: Sequence[ToolDefinition],
        compacted: int,
        recovered: int,
    ) -> ContextBreakdown:
        est = self.estimator

        # La conversacion se parte en dos capas porque se comportan distinto:
        # "observations" son tool results (enormes, compactables) y
        # "conversation" es lo que dijo el modelo (chico, no compactable).
        observations = [m for m in messages if m.role == "tool"]
        # El primer mensaje de usuario es la task; el resto de los "user" son
        # continuaciones que el loop inyecta (por ejemplo tras max_tokens).
        convo = [m for m in messages if m.role != "tool"][1:]

        def layer(name: str, texts: Sequence[str], *, compactable: bool = False) -> Layer:
            chars = sum(len(t) for t in texts if t)
            return Layer(
                name=name,
                tokens=sum(est.estimate(t) for t in texts if t),
                chars=chars,
                compactable=compactable,
            )

        tools_tokens = est.estimate_tools(definitions)
        layers = [
            layer("instructions", [system]),
            Layer(
                name="tools",
                tokens=tools_tokens,
                chars=sum(len(d.name) + len(d.description) + len(str(d.input_schema)) for d in definitions),
            ),
            layer("task", [task]),
            Layer(
                name="conversation",
                tokens=est.estimate_messages(convo),
                chars=sum(len(m.content or "") for m in convo),
            ),
            Layer(
                name="observations",
                tokens=est.estimate_messages(observations),
                chars=sum(len(m.content or "") for m in observations),
                compactable=True,
            ),
        ]
        # `skills` ya existe: el bloque de disclosure va DENTRO del system
        # prompt, asi que sus tokens ya estan contados en `instructions`. Se
        # reporta aparte igual, porque saber cuanto cuesta la capa es el punto de
        # medirla -- y el costo de una skill cargada aparece en `observations`,
        # que es donde vuelve como tool result.
        skills_tokens = est.estimate(_skills_block(system))
        layers.append(
            Layer(
                name="skills",
                tokens=skills_tokens,
                chars=len(_skills_block(system)),
                present=skills_tokens > 0,
            )
        )
        # Las capas que el roadmap nombra y LocalForge todavia no tiene.
        for absent in ("environment", "memory", "retrieved"):
            layers.append(Layer(name=absent, tokens=0, chars=0, present=False))

        order = {name: i for i, name in enumerate(LAYER_ORDER)}
        layers.sort(key=lambda l: order.get(l.name, 99))

        return ContextBreakdown(
            layers=tuple(layers),
            limit=self.budget.limit,
            reserved_output=self.budget.reserve_output,
            compacted_messages=compacted,
            recovered_tokens=recovered,
        )


def _skills_block(system: str) -> str:
    """Extrae el bloque de skills del system prompt, para poder medirlo aparte.

    Se mide sobre el prompt ya armado en vez de recibir las skills: asi la
    medicion no puede desincronizarse de lo que realmente se mando.
    """
    marker = "SKILLS DISPONIBLES"
    if marker not in system:
        return ""
    start = system.index(marker)
    end = system.find("\n\nResponde en el idioma", start)
    return system[start : end if end > 0 else len(system)]


__all__ = ["ContextBudget", "ContextBuilder", "BuiltContext"]
