"""Un comando por subcomando de la CLI.

Cada uno arma el harness que necesita y devuelve un exit code. La presentacion
vive en console.py; el ruteo, en app.py.
"""

from __future__ import annotations

import sys
from pathlib import Path

from localforge.cli.console import (
    BAD_MARK,
    DIM,
    GREEN,
    OK_MARK,
    RED,
    RULE,
    ConsoleSink,
    build_approver,
    c,
)
from localforge.config import DOTENV_APPLIED, find_dotenv, settings
from localforge.context import ContextBudget
from localforge.durable import FileCheckpointStore
from localforge.evals import localforge_suite, run_suite
from localforge.harness import AgentHarness
from localforge.models import AgentTask
from localforge.providers import build_provider
from localforge.providers.base import ProviderError
from localforge.sandbox import DenyingApprover, default_policy, read_only_policy
from localforge.tools import default_registry

async def cmd_health() -> int:
    provider = build_provider()
    try:
        info = await provider.health()
    except ProviderError as exc:
        print(c(f"{BAD_MARK} {exc}", RED))
        return 1
    finally:
        await provider.aclose()

    print(c(f"{OK_MARK} inferencia local operativa", GREEN))
    for key, value in info.items():
        print(f"  {key:18} {value}")
    print(f"  {'num_ctx':18} {settings.num_ctx}")
    budget = ContextBudget.from_settings(settings)
    print(f"  {'ctx disponible':18} {budget.available} (reserva {budget.reserve_output} para la salida)")
    print(f"  {'max_turns':18} {settings.max_turns}")
    policy = default_policy()
    print(f"  {'permisos':18} default={policy.default.value}, {len(policy.rules)} reglas")
    print(f"  {'wall_clock_s':18} {settings.wall_clock_s:g}s")

    # De donde salio la config: sin esto, un .env que no se esta leyendo es
    # indistinguible de uno que se lee y dice lo mismo.
    origin = find_dotenv()
    if origin:
        print(f"  {'config':18} {origin} ({len(DOTENV_APPLIED)} vars aplicadas)")
    else:
        print(f"  {'config':18} defaults del codigo + entorno (sin .env)")
    return 0


async def cmd_ask(
    repo: str,
    objective: str,
    *,
    verbose: bool,
    max_turns: int,
    read_only: bool = False,
    save: bool = False,
) -> int:
    repo_path = Path(repo).expanduser().resolve()
    if not repo_path.is_dir():
        print(c(f"{BAD_MARK} '{repo}' no es un directorio", RED))
        return 1

    provider = build_provider()
    try:
        info = await provider.health()
    except ProviderError as exc:
        print(c(f"{BAD_MARK} {exc}", RED))
        await provider.aclose()
        return 1

    registry = default_registry()
    policy = read_only_policy() if read_only else default_policy()
    harness = AgentHarness(
        provider,
        registry,
        on_event=ConsoleSink(verbose),
        policy=policy,
        approver=build_approver(read_only),
        checkpoints=FileCheckpointStore(settings.state_dir) if save else None,
    )

    task = AgentTask(
        objective=objective,
        repo_path=str(repo_path),
        max_turns=max_turns,
        wall_clock_s=settings.wall_clock_s,
        token_budget=settings.token_budget,
    )

    print(f"{c('repo', DIM)}   {repo_path}")
    print(f"{c('modelo', DIM)} {info['model']} @ {info['host']}")
    print(f"{c('tools', DIM)}  {', '.join(registry.names())}")
    modo = "solo lectura" if read_only else f"default (ASK -> {'consola' if sys.stdin.isatty() else 'denegado, sin TTY'})"
    print(f"{c('permisos', DIM)} {modo}")
    print(f"{c('tarea', DIM)}  {objective}")
    if save:
        print(f"{c('run id', DIM)} {task.id}  {c('(localforge resume <id> para retomar)', DIM)}")
    print()

    try:
        outcome = await harness.run(task)
    finally:
        await provider.aclose()

    print()
    color = GREEN if outcome.succeeded else RED
    print(c(f"{RULE} {outcome.summary()}", color))
    if outcome.state_path:
        print(c(f"   estados: {outcome.state_path}", DIM))
    if outcome.rejected_by:
        print(c(f"   rechazos: {', '.join(outcome.rejected_by)}", DIM))
    print()
    if outcome.output:
        print(outcome.output)
    return 0 if outcome.succeeded else 2


async def cmd_resume(task_id: str, *, verbose: bool) -> int:
    store = FileCheckpointStore(settings.state_dir)
    snapshot = store.load(task_id)
    if snapshot is None:
        print(c(f"{BAD_MARK} no hay ninguna corrida guardada con id '{task_id}'", RED))
        ids = store.list_ids()
        if ids:
            print("  disponibles: " + ", ".join(ids[:10]))
        else:
            print("  no hay ninguna. Corré con --save para guardar checkpoints.")
        return 1

    provider = build_provider()
    try:
        await provider.health()
    except ProviderError as exc:
        print(c(f"{BAD_MARK} {exc}", RED))
        await provider.aclose()
        return 1

    harness = AgentHarness(
        provider,
        default_registry(),
        on_event=ConsoleSink(verbose),
        policy=default_policy(),
        approver=build_approver(False),
        checkpoints=store,
    )
    print(f"{c('repo', DIM)}   {snapshot.task.repo_path}")
    print(f"{c('tarea', DIM)}  {snapshot.task.objective}")
    print(f"{c('desde', DIM)}  turno {snapshot.turn} · {snapshot.state_path}")
    print()
    try:
        outcome = await harness.resume(task_id)
    finally:
        await provider.aclose()

    print()
    print(c(f"{RULE} {outcome.summary()}", GREEN if outcome.succeeded else RED))
    print()
    if outcome.output:
        print(outcome.output)
    return 0 if outcome.succeeded else 2


async def cmd_eval(repo: str, *, verbose: bool) -> int:
    """Corre el dataset de golden tasks contra el LLM local."""
    repo_path = Path(repo).expanduser().resolve()
    if not repo_path.is_dir():
        print(c(f"{BAD_MARK} '{repo}' no es un directorio", RED))
        return 1

    provider = build_provider()
    try:
        info = await provider.health()
    except ProviderError as exc:
        print(c(f"{BAD_MARK} {exc}", RED))
        await provider.aclose()
        return 1

    suite = localforge_suite(str(repo_path))
    print(f"{c('repo', DIM)}   {repo_path}")
    print(f"{c('modelo', DIM)} {info['model']}")
    print(f"{c('tareas', DIM)} {len(suite)}")
    print()

    def progreso(result) -> None:  # noqa: ANN001
        mark = c(OK_MARK, GREEN) if result.passed else c(BAD_MARK, RED)
        print(f"  {mark} {result.task_id} ({result.outcome.turns} turnos, {result.wall_seconds:.0f}s)")
        for check in result.failed_checks:
            print(f"      {c('- ' + check.name + ': ' + check.detail, DIM)}")

    async def run_one(task):  # noqa: ANN001, ANN202
        harness = AgentHarness(
            provider,
            default_registry(),
            on_event=ConsoleSink(verbose) if verbose else None,
            policy=default_policy(),
            # En un eval no hay humano: los ASK se deniegan, que es lo correcto
            # y ademas hace el resultado reproducible.
            approver=DenyingApprover(),
        )
        return await harness.run(task)

    try:
        report = await run_suite(run_one, suite, label=info["model"], on_task=progreso)
    finally:
        await provider.aclose()

    print()
    print(report.render())
    return 0 if report.passed == report.total else 2


def cmd_runs() -> int:
    store = FileCheckpointStore(settings.state_dir)
    ids = store.list_ids()
    if not ids:
        print("no hay corridas guardadas. Corré `localforge ask ... --save`.")
        return 0
    print(f"{len(ids)} corrida(s) en {settings.state_dir / 'runs'}:\n")
    for task_id in ids:
        try:
            snap = store.load(task_id)
        except Exception as exc:  # noqa: BLE001 - un checkpoint roto no rompe el listado
            print(f"  {task_id}  {c(f'ilegible: {exc}', RED)}")
            continue
        if snap is None:
            continue
        print(f"  {task_id}")
        print(f"    {c(snap.task.objective[:70], DIM)}")
        print(
            f"    turno {snap.turn} · {snap.status.value} · "
            f"{snap.tokens_in + snap.tokens_out} tok · tools: {len(snap.trajectory)}"
        )
    return 0


__all__ = ["cmd_health", "cmd_ask", "cmd_resume", "cmd_eval", "cmd_runs"]
