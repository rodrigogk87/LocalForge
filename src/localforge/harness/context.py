"""Context engineering: el contexto es un presupuesto, no un buffer.

En la Fase 1 el contexto era trivial: `messages` crecia sin techo y la unica
defensa era truncar cada tool result a 8000 caracteres. Funciona hasta que no.

Este modulo introduce las cuatro piezas de la Fase 2:

  1. TokenEstimator   -- cuantos tokens cuesta un texto, sin tokenizer
  2. ContextBreakdown -- que capa se come el contexto, medido por turno
  3. ContextBudget    -- cuanto le toca a cada capa
  4. ContextBuilder   -- arma el contexto del turno y compacta si no entra

La idea central, y la razon de que esto sea un modulo y no tres ifs dentro del
loop: **el contexto no se acumula, se ASIGNA en cada turno**. El loop no decide
que entra; le pide al builder que lo decida y le reporte que hizo.

Dos cosas que este modulo NO hace todavia, a proposito:

- **Retrieval just-in-time** (W2-C11): traer el fragmento exacto que hace falta
  en vez de archivos enteros. Necesita poder buscar, y `search_code` no existe.
- **Aislamiento de contexto** (W2-C13): darle a cada subagente su propia
  ventana. Necesita subagentes, que son W8.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Sequence

from localforge.models import AgentMessage, ToolDefinition

# ---------------------------------------------------------------------------
# 1. Estimacion de tokens
# ---------------------------------------------------------------------------

# Un tokenizer real (tiktoken, el de qwen) seria exacto pero agrega una
# dependencia pesada y distinta por modelo. Y no hace falta: para decidir
# "¿entra o no?" alcanza una estimacion con error conocido y sesgo controlado.
#
# 3.6 caracteres por token es el punto de partida para codigo y prosa tecnica
# mezclados. El codigo tokeniza peor que la prosa (mas simbolos, mas
# identificadores raros), asi que el numero es mas bajo que el 4.0 que se cita
# para ingles corriente.
DEFAULT_CHARS_PER_TOKEN = 3.6

# Limites de cordura para la calibracion. Si el ratio se va afuera de esto, o
# el texto era rarisimo o hay un bug; en cualquier caso no le creemos.
_MIN_RATIO = 1.5
_MAX_RATIO = 8.0

# Peso de cada observacion nueva en la media movil. Bajo a proposito: preferimos
# converger despacio a saltar por un turno atipico.
_EMA_ALPHA = 0.25


class TokenEstimator:
    """Estima tokens por longitud, y se CALIBRA con los conteos reales.

    El truco que hace esto honesto: despues de cada llamada, Ollama devuelve
    `prompt_eval_count`, que es el conteo real de tokens de input. Comparamos
    nuestra estimacion contra ese numero y ajustamos el ratio.

    O sea que el estimador arranca siendo una heuristica y se convierte, a los
    pocos turnos, en una medicion calibrada contra el tokenizer de ESTE modelo.
    Sin dependencias y sin adivinar cual tokenizer usa.
    """

    def __init__(self, chars_per_token: float = DEFAULT_CHARS_PER_TOKEN) -> None:
        self.chars_per_token = chars_per_token
        self.samples = 0
        self._last_error_pct: float | None = None

    def estimate(self, text: str | None) -> int:
        if not text:
            return 0
        # El +1 evita que un texto cortito estime 0 tokens: todo texto cuesta.
        return int(len(text) / self.chars_per_token) + 1

    def estimate_messages(self, messages: Iterable[AgentMessage]) -> int:
        total = 0
        for m in messages:
            total += self.estimate(m.content)
            for call in m.tool_calls:
                # Los argumentos viajan serializados: cuestan tokens igual.
                total += self.estimate(call.name) + self.estimate(str(call.arguments))
            # Todo mensaje paga un overhead de estructura (rol, delimitadores).
            total += _MESSAGE_OVERHEAD
        return total

    def estimate_tools(self, definitions: Sequence[ToolDefinition]) -> int:
        total = 0
        for d in definitions:
            total += self.estimate(d.name) + self.estimate(d.description)
            total += self.estimate(str(d.input_schema))
        return total

    def observe(self, estimated: int, actual: int) -> None:
        """Recalibra el ratio con un conteo real.

        `actual` es `prompt_eval_count` de Ollama: los tokens que el modelo
        efectivamente leyo. Si estimamos de menos, el ratio real de caracteres
        por token es mas chico que el nuestro, y al revez.
        """
        if estimated <= 0 or actual <= 0:
            return
        implied = self.chars_per_token * (estimated / actual)
        if not (_MIN_RATIO <= implied <= _MAX_RATIO):
            return  # fuera de rango: no le creemos a esta muestra
        self._last_error_pct = (estimated - actual) / actual * 100
        self.chars_per_token += _EMA_ALPHA * (implied - self.chars_per_token)
        self.samples += 1

    @property
    def last_error_pct(self) -> float | None:
        """Error de la ultima estimacion, en porcentaje. Positivo = estimamos de mas."""
        return self._last_error_pct

    @property
    def calibrated(self) -> bool:
        return self.samples > 0


# Cada mensaje agrega delimitadores de rol al prompt. El numero exacto depende
# del template del modelo; 4 es la aproximacion habitual y el error que mete es
# irrelevante frente al del cuerpo del mensaje.
_MESSAGE_OVERHEAD = 4


# ---------------------------------------------------------------------------
# 2. Anatomia: que capa se come el contexto
# ---------------------------------------------------------------------------

# Los nombres son los de W2-C8. Las que todavia no existen en LocalForge se
# reportan igual, con present=False: ver un 0 explicito al lado de "retrieved"
# dice mas sobre el estado del proyecto que no listar la capa.
LAYER_ORDER = (
    "instructions",
    "tools",
    "task",
    "conversation",
    "observations",
    "environment",
    "skills",
    "memory",
    "retrieved",
)


@dataclass(frozen=True)
class Layer:
    name: str
    tokens: int
    chars: int
    # Si se puede tirar o resumir cuando falta lugar. instructions y task no:
    # sin el system prompt el agente no sabe trabajar, y sin la task no sabe
    # que tiene que hacer.
    compactable: bool = False
    present: bool = True


@dataclass(frozen=True)
class ContextBreakdown:
    """Foto del contexto de UN turno, por capa."""

    layers: tuple[Layer, ...]
    limit: int
    reserved_output: int
    compacted_messages: int = 0
    recovered_tokens: int = 0

    @property
    def total(self) -> int:
        return sum(l.tokens for l in self.layers)

    @property
    def available(self) -> int:
        """Lo que queda para input despues de reservar la salida."""
        return max(0, self.limit - self.reserved_output)

    @property
    def pct(self) -> float:
        return (self.total / self.available * 100) if self.available else 0.0

    @property
    def fits(self) -> bool:
        return self.total <= self.available

    def layer(self, name: str) -> Layer | None:
        return next((l for l in self.layers if l.name == name), None)

    def table(self) -> str:
        """Render para la CLI. La capa mas grande primero: es la que hay que atacar."""
        rows = []
        present = [l for l in self.layers if l.present]
        for l in sorted(present, key=lambda x: -x.tokens):
            share = (l.tokens / self.total * 100) if self.total else 0
            mark = "~" if l.compactable else " "
            rows.append(f"  {mark}{l.name:14} {l.tokens:>7} tok  {share:>5.1f}%")
        missing = [l.name for l in self.layers if not l.present]
        if missing:
            rows.append(f"   (sin usar: {', '.join(missing)})")
        head = f"  contexto: {self.total} / {self.available} tok ({self.pct:.1f}%)"
        if self.compacted_messages:
            head += f" · compactado: {self.compacted_messages} obs, -{self.recovered_tokens} tok"
        return head + "\n" + "\n".join(rows)


# ---------------------------------------------------------------------------
# 3. Presupuesto
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# 4. El builder
# ---------------------------------------------------------------------------

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


__all__ = [
    "ContextBudget",
    "ContextBreakdown",
    "ContextBuilder",
    "BuiltContext",
    "Layer",
    "TokenEstimator",
    "LAYER_ORDER",
    "DEFAULT_CHARS_PER_TOKEN",
]
