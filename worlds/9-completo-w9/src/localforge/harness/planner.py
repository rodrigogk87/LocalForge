"""Planner (W3): pensar el camino ANTES de caminarlo.

Sin planner, el modelo decide su proximo paso turno a turno mirando solo lo que
tiene enfrente. Funciona para preguntas de un paso ("¿que hace app.py?") y falla
de una forma muy reconocible en las de varios: el modelo lee lo primero que
encuentra, se siente satisfecho y responde. El verifier lo atrapa a veces; el
plan lo previene.

Lo que hace es poco a proposito: UNA llamada al modelo, **sin tools**, que
devuelve una lista numerada de pasos. Ese plan se agrega a la tarea y el loop
sigue como siempre. Tres decisiones:

1. **Sin tools durante el plan.** Si el planner pudiera leer archivos seria un
   agente adentro de otro, con su propio loop y sus propios presupuestos. Planear
   es decidir que mirar, no mirarlo.

2. **El plan no es un contrato.** Se le dice al modelo que puede desviarse si lo
   que lee lo contradice. Un plan escrito sin haber visto el repo esta mal en
   algun detalle casi siempre; obligarlo a seguirlo convierte un error de
   prediccion en un error de ejecucion.

3. **Un planner que falla no mata la corrida.** Si la respuesta no trae una
   lista legible, el agente arranca sin plan -- que es exactamente como
   trabajaba antes. El plan es una mejora, no una dependencia.

Cuesta una llamada extra por corrida. Por eso es opt-in (`--plan`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from localforge.models import AgentMessage, AgentTask, ModelResponse
from localforge.providers.base import ModelProvider

# Mas pasos que esto ya no es un plan, es una implementacion escrita a ciegas.
MAX_STEPS = 6

PLAN_PROMPT = """Antes de empezar, escribi un PLAN corto para esta tarea.

TAREA: {objective}

Reglas:
- Entre 2 y {max_steps} pasos, como lista numerada (1. 2. 3.).
- Cada paso es UNA accion concreta: que buscar, que archivo abrir, que comparar.
- Todavia no viste el repositorio: no inventes nombres de archivos. Escribí
  "buscar donde se define X" en vez de "abrir x.py".
- El ultimo paso es responder citando archivo y linea.
- Solo la lista, sin introduccion."""

_STEP = re.compile(r"^\s*(\d+)[.)]\s+(.+?)\s*$")


@dataclass(frozen=True)
class Plan:
    steps: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.steps)

    def render(self) -> str:
        return "\n".join(f"{i}. {s}" for i, s in enumerate(self.steps, 1))

    def as_task_suffix(self) -> str:
        return (
            "\n\nPLAN (lo escribiste vos antes de ver el repositorio; seguilo, pero si lo "
            "que leas lo contradice, cambialo y decilo):\n" + self.render()
        )


def parse_plan(text: str | None) -> Plan:
    """Extrae los pasos numerados. Lo que no es una linea numerada se ignora.

    Tolerante a proposito: los modelos chicos agregan "Aqui esta el plan:" aunque
    se les pida que no. Lo que importa son las lineas numeradas, en orden.
    """
    steps: list[str] = []
    for line in (text or "").splitlines():
        m = _STEP.match(line.replace("**", ""))
        if m and m.group(2).strip():
            steps.append(m.group(2).strip())
    return Plan(steps[:MAX_STEPS])


class Planner:
    def __init__(self, max_steps: int = MAX_STEPS) -> None:
        self.max_steps = max_steps

    async def plan(
        self, provider: ModelProvider, task: AgentTask, *, system: str | None = None
    ) -> tuple[Plan, ModelResponse]:
        prompt = PLAN_PROMPT.format(objective=task.objective, max_steps=self.max_steps)
        # tools=None: durante el plan no se puede actuar. Ver el docstring.
        response = await provider.complete(
            [AgentMessage(role="user", content=prompt)], None, system=system
        )
        plan = parse_plan(response.content)
        return Plan(plan.steps[: self.max_steps]), response


__all__ = ["Plan", "Planner", "parse_plan", "PLAN_PROMPT", "MAX_STEPS"]
