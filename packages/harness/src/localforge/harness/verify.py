"""Verificacion: la afirmacion del modelo no es evidencia.

Este modulo existe por un fallo concreto y medido. En la primera corrida real el
agente respondio sin leer un solo archivo, infiriendo de los nombres, y
concluyo que `cli.py` era el archivo mas importante **por su tamaño en KB**. Lo
atacamos con el system prompt y despues con `search_code`, y las dos veces
mejoro. Pero las dos son mejoras **probabilisticas**: el modelo puede ignorar la
instruccion y puede no usar la tool.

La diferencia entre una mejora y una garantia es quien decide. Mientras el
criterio de "termine" sea "el modelo dejo de pedir tools", el modelo es juez de
su propio trabajo. Un verifier mueve esa decision al harness, que puede mirar
**lo que el agente hizo** en vez de lo que dice que hizo.

La señal ya existia: `outcome.trajectory` tiene la lista de tools llamadas. No
hubo que instrumentar nada nuevo, solo dejar de ignorarla.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence, runtime_checkable

from localforge.models import AgentTask

# Tools que producen evidencia sobre el contenido del repo. `list_files` no esta
# a proposito: un listado dice como se LLAMAN las cosas, no que HACEN, y
# aceptarlo como evidencia es exactamente el fallo que este modulo previene.
EVIDENCE_TOOLS = frozenset({"read_file", "search_code"})

# Lenguaje especulativo. Si aparece describiendo codigo, el agente esta
# adivinando -- y el system prompt ya se lo prohibe explicitamente.
HEDGES = (
    "posiblemente",
    "probablemente",
    "parece que",
    "pareceria",
    "podria ser",
    "podría ser",
    "suponiendo que",
    "asumo que",
    "el mas probable",
    "el más probable",
    "no estoy seguro",
)


@dataclass(frozen=True)
class Verdict:
    """El resultado de verificar. `feedback` es texto dirigido AL MODELO.

    Si el verdict rechaza, el harness le manda `feedback` como un mensaje mas y
    lo deja reintentar. Por eso el mensaje no puede ser un diagnostico para
    humanos ("verificacion fallida"): tiene que decir que hacer distinto.
    """

    ok: bool
    check: str = ""
    feedback: str = ""

    @classmethod
    def passed(cls, check: str = "") -> Verdict:
        return cls(ok=True, check=check)

    @classmethod
    def rejected(cls, check: str, feedback: str) -> Verdict:
        return cls(ok=False, check=check, feedback=feedback)


@runtime_checkable
class Verifier(Protocol):
    """Mira un intento de respuesta final y decide si se acepta.

    Angosto a proposito, igual que `ModelProvider`: un metodo. El verifier NO
    puede ejecutar tools ni hablar con el modelo -- si pudiera, seria otro
    agente y habria que verificarlo a el.
    """

    name: str

    def verify(self, task: AgentTask, answer: str, trajectory: Sequence[str]) -> Verdict: ...


# ---------------------------------------------------------------------------
# Verifiers concretos
# ---------------------------------------------------------------------------


class TrajectoryVerifier:
    """Rechaza una respuesta que no se apoya en ninguna evidencia leida.

    Es el verifier mas simple posible y el que mas fallos atrapa, porque no
    juzga la calidad de la respuesta -- algo que requeriria otro modelo y traeria
    sus propios sesgos (W7-C45) -- sino una propiedad **estructural** de la
    trayectoria: si no leiste nada, no podes saber nada.

    Determinista, gratis, y sin falsos positivos posibles: o hay una llamada a
    read_file/search_code en la trayectoria, o no hay.
    """

    name = "trayectoria"

    def __init__(self, evidence_tools: frozenset[str] = EVIDENCE_TOOLS) -> None:
        self.evidence_tools = evidence_tools

    def verify(self, task: AgentTask, answer: str, trajectory: Sequence[str]) -> Verdict:
        used = [t for t in trajectory if t in self.evidence_tools]
        if used:
            return Verdict.passed(self.name)

        listed = "list_files" in trajectory
        detalle = (
            "Listaste los archivos pero no abriste ninguno. "
            if listed
            else "No usaste ninguna herramienta para mirar el repositorio. "
        )
        return Verdict.rejected(
            self.name,
            detalle
            + "Un listado de nombres no dice que hace el codigo. Antes de responder: "
            "usa search_code para ubicar lo que te preguntan y read_file para leerlo. "
            "Despues respondé citando archivo y linea.",
        )


class NoHedgingVerifier:
    """Rechaza una respuesta final que especula sobre el codigo.

    El system prompt ya prohibe estas palabras. Este verifier no reemplaza esa
    instruccion: la **hace cumplir**. Es la diferencia entre pedir y garantizar.

    Ojo con el falso positivo: hedgear sobre algo que efectivamente no se leyo es
    epistemicamente correcto. Por eso solo se aplica cuando el agente leyo poco:
    con evidencia abundante, un "probablemente" puntual es honestidad, no
    adivinanza.
    """

    name = "sin especulacion"

    def __init__(self, min_reads_to_allow_hedging: int = 3) -> None:
        self.min_reads = min_reads_to_allow_hedging

    def verify(self, task: AgentTask, answer: str, trajectory: Sequence[str]) -> Verdict:
        reads = sum(1 for t in trajectory if t in EVIDENCE_TOOLS)
        if reads >= self.min_reads:
            return Verdict.passed(self.name)

        low = answer.lower()
        found = [h for h in HEDGES if h in low]
        if not found:
            return Verdict.passed(self.name)

        return Verdict.rejected(
            self.name,
            f"Tu respuesta usa lenguaje especulativo ({', '.join(sorted(set(found))[:3])}) "
            f"y solo mirastes {reads} archivo(s). Esa palabra es la señal de que te falta leer: "
            "buscá y leé lo que te falta, y despues afirmá con la cita del archivo.",
        )


class CompositeVerifier:
    """Corre varios verifiers y devuelve el PRIMER rechazo.

    Primero y no todos a proposito: si se le devuelven tres reproches juntos, el
    modelo tiende a atender uno y dejar los otros. Un rechazo por vez produce
    una reparacion por vez, que es mas facil de verificar despues.
    """

    name = "compuesto"

    def __init__(self, verifiers: Sequence[Verifier]) -> None:
        self.verifiers = list(verifiers)

    def verify(self, task: AgentTask, answer: str, trajectory: Sequence[str]) -> Verdict:
        for v in self.verifiers:
            verdict = v.verify(task, answer, trajectory)
            if not verdict.ok:
                return verdict
        return Verdict.passed(self.name)


def default_verifier() -> CompositeVerifier:
    """El verifier de la Fase 3: estructural primero, lexico despues.

    El orden importa: si no leyo nada, el reproche util es "lee algo", no "no
    especules". Arreglar la causa antes que el sintoma.
    """
    return CompositeVerifier([TrajectoryVerifier(), NoHedgingVerifier()])


__all__ = [
    "Verdict",
    "Verifier",
    "TrajectoryVerifier",
    "NoHedgingVerifier",
    "CompositeVerifier",
    "default_verifier",
    "EVIDENCE_TOOLS",
    "HEDGES",
]
