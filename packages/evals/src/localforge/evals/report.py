"""El reporte: pass rate, taxonomia de fallos, costo, y la comparacion.

La taxonomia sale gratis porque `FailureReason` es un enum cerrado desde el
Mundo 1, con este momento en mente: agrupar 200 corridas por motivo es un
`Counter` sobre un campo que ya existe.
"""

from __future__ import annotations

import statistics
from collections import Counter
from dataclasses import dataclass, field

from localforge.evals.checks import CheckResult
from localforge.models import AgentOutcome

@dataclass
class TaskResult:
    task_id: str
    tags: tuple[str, ...]
    outcome: AgentOutcome
    checks: list[CheckResult]
    wall_seconds: float

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks)

    @property
    def failed_checks(self) -> list[CheckResult]:
        return [c for c in self.checks if not c.passed]


@dataclass
class EvalReport:
    results: list[TaskResult] = field(default_factory=list)
    label: str = ""

    # -- agregados ---------------------------------------------------------

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r.passed)

    @property
    def pass_rate(self) -> float:
        return (self.passed / self.total * 100) if self.total else 0.0

    def failure_taxonomy(self) -> Counter[str]:
        """Agrupa los fallos por motivo. Esto es gratis porque FailureReason es
        un enum cerrado: con strings libres no se podria agrupar nada."""
        c: Counter[str] = Counter()
        for r in self.results:
            if r.outcome.reason is not None:
                c[r.outcome.reason.value] += 1
            elif not r.passed:
                # Termino bien pero no paso los checks: es un fallo de CALIDAD,
                # que es una categoria distinta a un fallo de EJECUCION.
                c["calidad"] += 1
        return c

    def failed_check_counts(self) -> Counter[str]:
        """Que check falla mas. Dice donde esta el problema, no solo que lo hay."""
        c: Counter[str] = Counter()
        for r in self.results:
            for check in r.failed_checks:
                c[check.name] += 1
        return c

    def cost(self) -> dict[str, float]:
        if not self.results:
            return {}
        tokens = [r.outcome.total_tokens for r in self.results]
        turns = [r.outcome.turns for r in self.results]
        secs = [r.wall_seconds for r in self.results]
        return {
            "tokens_total": sum(tokens),
            "tokens_media": statistics.mean(tokens),
            "tokens_max": max(tokens),
            "turnos_media": statistics.mean(turns),
            "segundos_total": sum(secs),
            "segundos_media": statistics.mean(secs),
            "segundos_max": max(secs),
        }

    # -- render ------------------------------------------------------------

    def render(self) -> str:
        head = f"{self.passed}/{self.total} tareas pasaron ({self.pass_rate:.0f}%)"
        if self.label:
            head = f"[{self.label}] {head}"
        lines = [head, ""]

        for r in self.results:
            mark = "OK  " if r.passed else "FALLA"
            lines.append(f"  {mark} {r.task_id}  ({r.outcome.turns} turnos, {r.outcome.total_tokens} tok, {r.wall_seconds:.1f}s)")
            for c in r.failed_checks:
                lines.append(f"         - {c.name}: {c.detail}")

        taxonomia = self.failure_taxonomy()
        if taxonomia:
            lines += ["", "  taxonomia de fallos:"]
            for motivo, n in taxonomia.most_common():
                lines.append(f"    {motivo:22} {n}")

        checks = self.failed_check_counts()
        if checks:
            lines += ["", "  checks que mas fallan:"]
            for name, n in checks.most_common(5):
                lines.append(f"    {name:22} {n}")

        cost = self.cost()
        if cost:
            lines += [
                "",
                f"  costo: {int(cost['tokens_total'])} tokens en total, "
                f"{cost['tokens_media']:.0f} de media, {int(cost['tokens_max'])} el peor",
                f"  tiempo: {cost['segundos_total']:.1f}s en total, "
                f"{cost['segundos_media']:.1f}s de media, {cost['segundos_max']:.1f}s el peor",
            ]
        return "\n".join(lines)


def compare(a: EvalReport, b: EvalReport) -> str:
    """Diff entre dos reportes: el benchmark de harnesses de W7-C49.

    Lo importante es que compara **por tarea**, no solo los promedios. Dos
    harnesses con el mismo 75% pueden fallar tareas distintas, y eso es
    exactamente lo que hay que ver antes de decidir cual es mejor.
    """
    lines = [
        f"{a.label or 'A'}: {a.passed}/{a.total} ({a.pass_rate:.0f}%)   "
        f"{b.label or 'B'}: {b.passed}/{b.total} ({b.pass_rate:.0f}%)",
        "",
    ]
    by_id_a = {r.task_id: r for r in a.results}
    by_id_b = {r.task_id: r for r in b.results}

    for task_id in sorted(set(by_id_a) | set(by_id_b)):
        ra, rb = by_id_a.get(task_id), by_id_b.get(task_id)
        if ra is None or rb is None:
            lines.append(f"  {task_id:32} solo en {'B' if ra is None else 'A'}")
            continue
        if ra.passed == rb.passed:
            simbolo = "==" if ra.passed else "xx"
        else:
            simbolo = "->" if rb.passed else "<-"
        delta_tok = rb.outcome.total_tokens - ra.outcome.total_tokens
        lines.append(
            f"  {simbolo} {task_id:32} "
            f"{ra.outcome.total_tokens:>6} -> {rb.outcome.total_tokens:>6} tok "
            f"({delta_tok:+d})"
        )

    mejoras = sum(1 for i in set(by_id_a) & set(by_id_b) if not by_id_a[i].passed and by_id_b[i].passed)
    regresiones = sum(1 for i in set(by_id_a) & set(by_id_b) if by_id_a[i].passed and not by_id_b[i].passed)
    lines += ["", f"  {mejoras} mejora(s), {regresiones} regresion(es)"]
    if regresiones:
        lines.append("  OJO: una regresion puede esconderse detras de un pass rate que subio.")
    return "\n".join(lines)


__all__ = ["TaskResult", "EvalReport", "compare"]
