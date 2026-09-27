"""Correr el dataset.

`run_suite` recibe una funcion `run(task) -> outcome` y no un harness, para que
se pueda evaluar cualquier cosa que produzca un outcome: dos configuraciones del
harness, dos modelos, o un mock en los tests. Eso es lo que hace posible el
benchmark de W7-C49.
"""

from __future__ import annotations

import time
from typing import Awaitable, Callable, Sequence

from localforge.evals.dataset import GoldenTask
from localforge.evals.report import EvalReport, TaskResult
from localforge.models import AgentOutcome, AgentTask

HarnessFactory = Callable[[], object]
RunFn = Callable[[AgentTask], Awaitable[AgentOutcome]]


async def run_suite(
    run: RunFn,
    tasks: Sequence[GoldenTask],
    *,
    label: str = "",
    on_task: Callable[[TaskResult], None] | None = None,
) -> EvalReport:
    """Corre el dataset y devuelve el reporte.

    Recibe una funcion `run(task) -> outcome` en vez de un harness para que se
    pueda evaluar cualquier cosa que produzca un outcome: dos configuraciones
    distintas del harness, dos modelos, o un mock en los tests. Eso es lo que
    hace posible el benchmark de W7-C49.
    """
    report = EvalReport(label=label)
    for golden in tasks:
        started = time.monotonic()
        outcome = await run(golden.to_task())
        elapsed = time.monotonic() - started
        result = TaskResult(
            task_id=golden.id,
            tags=golden.tags,
            outcome=outcome,
            checks=[c.run(outcome) for c in golden.checks],
            wall_seconds=elapsed,
        )
        report.results.append(result)
        if on_task:
            on_task(result)
    return report


__all__ = ["run_suite", "RunFn"]
