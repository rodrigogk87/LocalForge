"""Maquina de estados del agente.

En la Fase 1 el estado era un campo informativo: `AgentStatus` existia pero
nadie lo movia durante la corrida, y cualquier valor podia seguir a cualquier
otro. Eso no es una maquina de estados, es una etiqueta.

Lo que la convierte en una maquina son las **transiciones prohibidas**. Si todo
puede seguir a todo, el tipo no te protege de nada: un bug que marque
COMPLETED en el medio de la ejecucion pasa desapercibido, y el dia que haya
checkpoints (Fase 6) vas a poder restaurar un estado imposible.

Decision explicita: **no existe el estado PLANNING**, aunque el roadmap lo
mencione. No hay planner todavia. Un estado por el que el agente pasa sin hacer
nada es decoracion que miente sobre lo que el sistema hace. Se agrega el dia que
se agregue el planner, junto con sus transiciones.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from localforge.models import AgentStatus


class IllegalTransition(RuntimeError):
    """Se intento una transicion que la maquina no permite.

    Es un error NUESTRO, no del modelo ni del usuario: significa que el loop
    tiene un bug de flujo. Por eso es una excepcion y no un ToolResult.
    """


# El grafo. Leelo como "desde X se puede ir a...".
#
#   CREATED ──> RUNNING ──> WAITING_TOOL ──┐
#                  ^                       │
#                  └───────────────────────┘
#                  │
#                  └──> VERIFYING ──> COMPLETED
#                            │
#                            └──> REPAIRING ──> RUNNING
#
# Cualquier estado no terminal puede caer en FAILED o CANCELLED: los
# presupuestos y la cancelacion cortan desde donde sea.
_ALLOWED: dict[AgentStatus, frozenset[AgentStatus]] = {
    AgentStatus.CREATED: frozenset({AgentStatus.RUNNING}),
    AgentStatus.RUNNING: frozenset({AgentStatus.WAITING_TOOL, AgentStatus.VERIFYING}),
    AgentStatus.WAITING_TOOL: frozenset({AgentStatus.RUNNING}),
    AgentStatus.VERIFYING: frozenset({AgentStatus.COMPLETED, AgentStatus.REPAIRING}),
    AgentStatus.REPAIRING: frozenset({AgentStatus.RUNNING}),
    AgentStatus.COMPLETED: frozenset(),
    AgentStatus.FAILED: frozenset(),
    AgentStatus.CANCELLED: frozenset(),
}

# Salidas de emergencia. Van aparte del grafo normal porque no describen el
# flujo del trabajo sino su interrupcion.
_ABORTS = frozenset({AgentStatus.FAILED, AgentStatus.CANCELLED})


@dataclass(frozen=True)
class Transition:
    """Un paso del ciclo de vida. La lista de estos es la historia de la corrida."""

    frm: AgentStatus
    to: AgentStatus
    turn: int
    at: datetime
    note: str = ""


@dataclass
class StateMachine:
    """Estado actual mas la historia de como se llego.

    La historia no es un lujo de logging: es lo que permite responder "¿este
    agente reparo o le salio bien de una?" sin instrumentar nada mas. En la
    Fase 7 es una señal de eval (W7-C46).
    """

    status: AgentStatus = AgentStatus.CREATED
    history: list[Transition] = field(default_factory=list)

    def can(self, to: AgentStatus) -> bool:
        if self.status.is_terminal:
            return False
        if to in _ABORTS:
            return True
        return to in _ALLOWED[self.status]

    def to(self, target: AgentStatus, *, turn: int = 0, note: str = "") -> None:
        if not self.can(target):
            if self.status.is_terminal:
                raise IllegalTransition(
                    f"'{self.status.value}' es terminal: no se puede pasar a '{target.value}'"
                )
            legal = ", ".join(sorted(s.value for s in _ALLOWED[self.status] | _ABORTS))
            raise IllegalTransition(
                f"transicion prohibida {self.status.value} -> {target.value}. "
                f"Desde '{self.status.value}' se puede ir a: {legal}"
            )
        self.history.append(
            Transition(
                frm=self.status,
                to=target,
                turn=turn,
                at=datetime.now(timezone.utc),
                note=note,
            )
        )
        self.status = target

    # -- lectura -----------------------------------------------------------

    @property
    def repairs(self) -> int:
        """Cuantas veces el verifier rechazo y el agente volvio a intentar."""
        return sum(1 for t in self.history if t.to == AgentStatus.REPAIRING)

    def path(self) -> list[str]:
        """La secuencia de estados, para logs y para el outcome."""
        if not self.history:
            return [self.status.value]
        return [self.history[0].frm.value] + [t.to.value for t in self.history]

    def compact_path(self) -> str:
        """Igual que path() pero colapsa lo que se repite.

        Una corrida de 11 turnos produce 20+ estados, casi todos alternando
        `running -> waiting_tool`. Sin colapsar, el camino es ilegible justo
        cuando mas interesa leerlo. Se colapsan dos patrones:

          - el mismo estado repetido        -> `running x3`
          - un ciclo de dos que se repite   -> `(running -> waiting_tool) x5`

        El ciclo de dos es el que importa: es la forma de un agente trabajando.
        """
        names = self.path()
        out: list[str] = []
        i = 0
        while i < len(names):
            # Ciclo A -> B -> A -> B... Se exige verlo al menos dos veces antes
            # de colapsarlo, para no inventar un ciclo donde hay una casualidad.
            if i + 3 < len(names) and names[i] == names[i + 2] and names[i + 1] == names[i + 3]:
                a, b = names[i], names[i + 1]
                n = 0
                while i + 1 < len(names) and names[i] == a and names[i + 1] == b:
                    n += 1
                    i += 2
                out.append(f"({a} -> {b}) x{n}")
                continue
            # Repeticion simple del mismo estado.
            j = i
            while j + 1 < len(names) and names[j + 1] == names[i]:
                j += 1
            out.append(names[i] if j == i else f"{names[i]} x{j - i + 1}")
            i = j + 1
        return " -> ".join(out)


__all__ = ["StateMachine", "Transition", "IllegalTransition"]
