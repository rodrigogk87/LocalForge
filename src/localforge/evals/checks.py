"""Afirmaciones verificables sobre el resultado de una corrida.

Todo determinista, sin model-as-judge. Un juez LLM trae sus propios sesgos -- de
posicion, de verbosidad, de autocomplacencia (W7-C45) -- y antes de medir con una
regla torcida conviene medir lo que se puede medir exacto.

Los checks corren sobre el `AgentOutcome` COMPLETO, no solo sobre el texto: por
eso el mismo mecanismo mide la respuesta, el camino y el costo. Dos agentes que
dan la misma respuesta final no son equivalentes si uno leyo tres archivos y el
otro treinta (W7-C46).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from localforge.harness.verify import HEDGES
from localforge.models import AgentOutcome, AgentStatus


@dataclass(frozen=True)
class CheckResult:
    name: str
    passed: bool
    detail: str = ""


class Check:
    """Una afirmacion verificable sobre el resultado de una corrida.

    Se evalua sobre el `AgentOutcome` completo, no solo sobre el texto: eso es
    lo que permite medir la trayectoria y el costo con el mismo mecanismo.
    """

    name = "check"

    def run(self, outcome: AgentOutcome) -> CheckResult:  # pragma: no cover - interface
        raise NotImplementedError


@dataclass(frozen=True)
class Succeeded(Check):
    """Lo minimo: que haya terminado bien."""

    name: str = "termino bien"

    def run(self, outcome: AgentOutcome) -> CheckResult:
        if outcome.status is AgentStatus.COMPLETED:
            return CheckResult(self.name, True)
        motivo = outcome.reason.value if outcome.reason else "sin motivo"
        return CheckResult(self.name, False, f"{outcome.status.value} ({motivo})")


@dataclass(frozen=True)
class Mentions(Check):
    """La respuesta tiene que nombrar estas cosas.

    Es el check mas directo de grounding: si la pregunta era "donde se valida X"
    y la respuesta no nombra el archivo donde esta, no importa que tan linda sea.
    """

    expected: tuple[str, ...]
    name: str = "menciona lo esperado"

    def run(self, outcome: AgentOutcome) -> CheckResult:
        low = outcome.output.lower()
        faltan = [e for e in self.expected if e.lower() not in low]
        if not faltan:
            return CheckResult(self.name, True)
        return CheckResult(self.name, False, f"no menciona: {', '.join(faltan)}")


@dataclass(frozen=True)
class UsedTool(Check):
    """Tuvo que haber usado esta tool. Mide el CAMINO, no el resultado."""

    tool: str
    at_least: int = 1
    name: str = ""

    def run(self, outcome: AgentOutcome) -> CheckResult:
        n = outcome.trajectory.count(self.tool)
        label = self.name or f"uso {self.tool}"
        if n >= self.at_least:
            return CheckResult(label, True)
        return CheckResult(label, False, f"{n} vez/veces, esperaba >= {self.at_least}")


@dataclass(frozen=True)
class NoHedging(Check):
    """Sin lenguaje especulativo. Lo mismo que verifica el verifier, medido."""

    name: str = "sin especulacion"

    def run(self, outcome: AgentOutcome) -> CheckResult:
        low = outcome.output.lower()
        found = sorted({h for h in HEDGES if h in low})
        if not found:
            return CheckResult(self.name, True)
        return CheckResult(self.name, False, ", ".join(found[:3]))


@dataclass(frozen=True)
class CitesFileAndLine(Check):
    """Cita archivo y linea en algun lugar: `algo.py:31` o `algo.py linea 31`."""

    name: str = "cita archivo y linea"
    _pattern = re.compile(r"[\w/.-]+\.\w+\s*[:(]?\s*(?:linea\s*)?\d+", re.IGNORECASE)

    def run(self, outcome: AgentOutcome) -> CheckResult:
        if self._pattern.search(outcome.output):
            return CheckResult(self.name, True)
        return CheckResult(self.name, False, "ninguna cita con numero de linea")


@dataclass(frozen=True)
class WithinBudget(Check):
    """Costo y latencia son parte del resultado, no una nota al pie (W7-C47)."""

    max_turns: int | None = None
    max_tokens: int | None = None
    max_seconds: float | None = None
    name: str = "dentro del presupuesto"

    def run(self, outcome: AgentOutcome) -> CheckResult:
        excedidos: list[str] = []
        if self.max_turns is not None and outcome.turns > self.max_turns:
            excedidos.append(f"{outcome.turns} turnos > {self.max_turns}")
        if self.max_tokens is not None and outcome.total_tokens > self.max_tokens:
            excedidos.append(f"{outcome.total_tokens} tokens > {self.max_tokens}")
        if self.max_seconds is not None and outcome.duration_ms / 1000 > self.max_seconds:
            excedidos.append(f"{outcome.duration_ms / 1000:.1f}s > {self.max_seconds}s")
        if not excedidos:
            return CheckResult(self.name, True)
        return CheckResult(self.name, False, "; ".join(excedidos))


__all__ = [
    "Check", "CheckResult", "Succeeded", "Mentions", "UsedTool",
    "NoHedging", "CitesFileAndLine", "WithinBudget",
]
