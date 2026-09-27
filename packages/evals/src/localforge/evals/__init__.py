"""Agent Evals (W7): medir el agente con datos, no con impresiones.

Los tests del repo testean el HARNESS: que el loop termine, que un permiso
deniegue, que un checkpoint se restaure. Nada de eso dice si el agente es BUENO.
Un agente puede pasar los 184 y contestar pavadas. Este paquete mide lo otro.
"""

from localforge.evals.checks import (
    Check,
    CheckResult,
    CitesFileAndLine,
    Mentions,
    NoHedging,
    Succeeded,
    UsedTool,
    WithinBudget,
)
from localforge.evals.dataset import GoldenTask, localforge_suite
from localforge.evals.report import EvalReport, TaskResult, compare
from localforge.evals.runner import run_suite

__all__ = [
    "Check", "CheckResult", "Succeeded", "Mentions", "UsedTool",
    "NoHedging", "CitesFileAndLine", "WithinBudget",
    "GoldenTask", "localforge_suite",
    "TaskResult", "EvalReport", "compare", "run_suite",
]
