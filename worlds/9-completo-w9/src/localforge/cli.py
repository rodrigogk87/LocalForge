"""CLI de LocalForge.

    lfw9 health
    lfw9 ask <repo> "<objetivo>" [--plan] [--memory] [--sandbox] [--mcp f.json] [--edit] [--delegate]
    lfw9 eval [repo] [--judge]
    lfw9 submit <repo> "<objetivo>"  /  lfw9 worker [--once]  /  lfw9 jobs

Interfaz minima a proposito: la UI no importa hasta que el harness funcione.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import dataclasses
import sys
import time
from pathlib import Path

from localforge.config import DOTENV_APPLIED, find_dotenv, settings
from localforge.harness import AgentHarness
from localforge.harness.subagent import registry_with_subagents
from localforge.evals import localforge_suite, run_suite
from localforge.harness.checkpoint import FileCheckpointStore
from localforge.harness.context import ContextBreakdown, ContextBudget
from localforge.models import ToolCall as _ToolCall
from localforge.permissions import (
    Approver,
    DenyingApprover,
    default_policy,
    read_only_policy,
)
from localforge.models import AgentTask, ModelResponse, ToolCall, ToolResult
from localforge.providers import build_provider
from localforge.providers.base import ProviderError
from localforge.tools import default_registry
from localforge.harness.memory import FileMemoryStore
from localforge.harness.planner import Planner
from localforge.harness.queue import FileQueue, Worker, run_or_resume
from localforge.harness.worktree import EditInWorktreeTool
from localforge.judge import LLMJudge
from localforge.mcp import MCPError, MCPSession, load_config
from localforge.sandbox import DockerSandbox
from localforge.tools.command import command_policy, registry_with_sandbox

DIM = "\033[2m"
BOLD = "\033[1m"
CYAN = "\033[36m"
GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
OFF = "\033[0m"


def _init_console() -> bool:
    """La consola de Windows usa cp1252 y revienta con cualquier glifo no-latin1.

    Intentamos pasarla a UTF-8; si no se puede, devolvemos False y la salida
    cae a marcas ASCII. Un agente que muere imprimiendo un tilde es un agente
    que "falla" por una razon que no tiene nada que ver con el agente.
    """
    ok = True
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, OSError, ValueError):
            ok = False
    return ok


UNICODE_OK = _init_console()

OK_MARK = "✓" if UNICODE_OK else "[ok]"
BAD_MARK = "✗" if UNICODE_OK else "[x]"
ARROW = "→" if UNICODE_OK else "->"
RULE = "──" if UNICODE_OK else "--"
PIPE = "│" if UNICODE_OK else "|"


def _supports_color() -> bool:
    return sys.stdout.isatty()


def _c(text: str, color: str) -> str:
    return f"{color}{text}{OFF}" if _supports_color() else text


# ---------------------------------------------------------------------------


class ConsoleSink:
    """Muestra el loop mientras corre.

    Un agente que tarda un minuto sin decir nada es indistinguible de uno
    colgado. Ver las tool calls en vivo es la observabilidad minima.
    """

    def __init__(self, verbose: bool = False) -> None:
        self.verbose = verbose
        self._t0 = time.monotonic()

    def __call__(self, event: str, **payload: object) -> None:
        stamp = _c(f"[{time.monotonic() - self._t0:6.1f}s]", DIM)

        if event == "resumed":
            print(_c(f"{stamp} retomando desde el turno {payload['turn']}", YELLOW))

        elif event == "planned":
            plan = payload["plan"]
            if plan:
                print(f"{stamp} {_c(RULE + ' plan', BOLD)}")
                for i, step in enumerate(plan.steps, 1):
                    print(f"{stamp}   {_c(f'{i}. {step[:110]}', DIM)}")
            else:
                print(_c(f"{stamp} sin plan (la respuesta no traia una lista)", YELLOW))

        elif event == "plan_failed":
            print(_c(f"{stamp} el planner fallo ({payload['error']}); se sigue sin plan", YELLOW))

        elif event == "memory_recalled":
            entries = payload["entries"]
            print(_c(f"{stamp} memoria: {len(entries)} corrida(s) anterior(es) relacionada(s)", DIM))

        elif event == "turn_start":
            print(f"{stamp} {_c(RULE + ' turno ' + str(payload['turn']), BOLD)}")

        elif event == "context_built":
            bd = payload["breakdown"]
            assert isinstance(bd, ContextBreakdown)
            # Sin verbose, una linea: lo que se gasto y si hubo que compactar.
            warn = bd.pct >= 75
            line = f"{bd.total}/{bd.available} tok ({bd.pct:.0f}%)"
            if bd.compacted_messages:
                line += f" · compactado {bd.compacted_messages} obs (-{bd.recovered_tokens} tok)"
            print(f"{stamp}   ctx: {_c(line, YELLOW if warn else DIM)}")
            if self.verbose:
                for row in bd.table().splitlines()[1:]:
                    print(f"{stamp} {_c(row, DIM)}")

        elif event == "model_response":
            response = payload["response"]
            assert isinstance(response, ModelResponse)
            meta = _c(
                f"{response.input_tokens}{ARROW}{response.output_tokens} tok | {response.duration_ms / 1000:.1f}s",
                DIM,
            )
            print(f"{stamp}   modelo: {response.stop_reason.value} · {meta}")
            if response.content and self.verbose:
                head = response.content.strip().splitlines()[:3]
                for line in head:
                    print(f"{stamp}   {_c(PIPE + ' ' + line[:110], DIM)}")

        elif event == "verified":
            verdict = payload["verdict"]
            if verdict.ok:
                print(f"{stamp}   {_c(OK_MARK, GREEN)} verificado ({verdict.check})")
            else:
                print(f"{stamp}   {_c(BAD_MARK + ' rechazado por ' + verdict.check, YELLOW)}")
                if self.verbose:
                    print(f"{stamp}   {_c(PIPE + ' ' + verdict.feedback[:150], DIM)}")

        elif event == "tools_start":
            calls = payload["calls"]
            assert isinstance(calls, list)
            for call in calls:
                assert isinstance(call, ToolCall)
                args = ", ".join(f"{k}={v!r}" for k, v in call.arguments.items())
                print(f"{stamp}   {_c(ARROW, CYAN)} {call.name}({args[:100]})")

        elif event == "tools_done":
            results = payload["results"]
            assert isinstance(results, list)
            for result in results:
                assert isinstance(result, ToolResult)
                if result.success:
                    size = len(result.output or "")
                    mark = _c(OK_MARK, GREEN)
                    extra = _c(f"{size} chars{' · truncado' if result.truncated else ''}", DIM)
                    print(f"{stamp}   {mark} {result.name} {extra}")
                else:
                    print(f"{stamp}   {_c(BAD_MARK, RED)} {result.name}: {result.error}")


# ---------------------------------------------------------------------------


class ConsoleApprover:
    """Aprobacion humana por consola.

    Muestra la tool, sus argumentos y el motivo, y espera un si explicito.
    Cualquier cosa que no sea "s" o "y" es un no: un enter distraido no puede
    autorizar una escritura.
    """

    def __init__(self) -> None:
        self.recordar: dict[str, bool] = {}

    async def approve(self, call: _ToolCall, reason: str) -> bool:
        if call.name in self.recordar:
            return self.recordar[call.name]

        args = ", ".join(f"{k}={v!r}" for k, v in call.arguments.items())
        print()
        print(_c(f"  {BAD_MARK} el agente pide permiso", YELLOW))
        print(f"    tool   {call.name}({args[:160]})")
        print(f"    motivo {reason}")
        try:
            # asyncio.to_thread para no bloquear el event loop mientras el
            # humano piensa: si hay tools corriendo en paralelo, siguen.
            respuesta = (
                await asyncio.to_thread(input, "    ¿permitir? [s/N/t=siempre esta tool] ")
            ).strip().lower()
        except (EOFError, KeyboardInterrupt):
            print(_c("    sin respuesta -> denegado", DIM))
            return False

        if respuesta == "t":
            self.recordar[call.name] = True
            return True
        ok = respuesta in ("s", "si", "y", "yes")
        print(_c(f"    -> {'permitido' if ok else 'denegado'}", GREEN if ok else RED))
        return ok


def _build_approver(read_only: bool) -> Approver:
    """Quien resuelve los ASK.

    Sin TTY no hay humano, y un ASK que nadie puede contestar es un DENY. Que la
    eleccion dependa del entorno y no de un flag es deliberado: en CI, donde mas
    importa, no hay forma de olvidarse de pasar el flag seguro.
    """
    if read_only or not sys.stdin.isatty():
        return DenyingApprover()
    return ConsoleApprover()


async def cmd_health() -> int:
    provider = build_provider()
    try:
        info = await provider.health()
    except ProviderError as exc:
        print(_c(f"{BAD_MARK} {exc}", RED))
        return 1
    finally:
        await provider.aclose()

    print(_c(f"{OK_MARK} inferencia local operativa", GREEN))
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
    delegate: bool = False,
    plan: bool = False,
    memory: bool = False,
    sandbox: bool = False,
    mcp: str | None = None,
    edit: bool = False,
) -> int:
    repo_path = Path(repo).expanduser().resolve()
    if not repo_path.is_dir():
        print(_c(f"{BAD_MARK} '{repo}' no es un directorio", RED))
        return 1

    provider = build_provider()
    try:
        info = await provider.health()
    except ProviderError as exc:
        print(_c(f"{BAD_MARK} {exc}", RED))
        await provider.aclose()
        return 1

    policy = read_only_policy() if read_only else default_policy()
    approver = _build_approver(read_only)
    # Con --delegate el registry suma la tool `delegate`, que arma un subagente
    # con su propio contexto. Es opt-in porque multiplica el costo: cada
    # delegacion es un agente entero corriendo.
    registry = (
        registry_with_subagents(provider, cfg=settings, policy=policy, approver=approver)
        if delegate
        else default_registry()
    )
    notas: list[str] = []

    # --sandbox: run_command, solo si Docker esta. Sin Docker, la tool no se
    # registra Y la politica la deniega (fail-closed).
    if sandbox:
        sb = DockerSandbox(settings.sandbox_image)
        registry, nota = await registry_with_sandbox(registry, sb)
        policy = command_policy("run_command" in registry.names(), policy)
        notas.append(nota)

    # --edit: delegate_edit, un subagente que edita en un git worktree.
    if edit:
        registry.register(EditInWorktreeTool(provider, cfg=settings))
        notas.append("delegate_edit: los cambios vuelven como diff, tu repo no se toca")

    # --mcp: la config se pasa explicita. Nunca se lee del repo analizado.
    mcp_session = None
    if mcp:
        try:
            mcp_session = MCPSession(load_config(mcp))
        except (OSError, ValueError, MCPError) as exc:
            print(_c(f"{BAD_MARK} config MCP invalida: {exc}", RED))
            await provider.aclose()
            return 1

    harness = AgentHarness(
        provider,
        registry,
        on_event=ConsoleSink(verbose),
        policy=policy,
        approver=approver,
        checkpoints=FileCheckpointStore(settings.state_dir) if save else None,
        planner=Planner() if plan else None,
        memory=FileMemoryStore(settings.state_dir) if memory else None,
    )

    task = AgentTask(
        objective=objective,
        repo_path=str(repo_path),
        max_turns=max_turns,
        wall_clock_s=settings.wall_clock_s,
        token_budget=settings.token_budget,
    )

    async with contextlib.AsyncExitStack() as stack:
        if mcp_session is not None:
            await stack.enter_async_context(mcp_session)
            nombres = mcp_session.register_into(registry)
            notas.append(f"MCP: {len(nombres)} tool(s) de {len(mcp_session.clients)} server(s)")
            notas.extend(f"MCP no arranco {e}" for e in mcp_session.errors)

        print(f"{_c('repo', DIM)}   {repo_path}")
        print(f"{_c('modelo', DIM)} {info['model']} @ {info['host']}")
        print(f"{_c('tools', DIM)}  {', '.join(registry.names())}")
        modo = "solo lectura" if read_only else f"default (ASK -> {'consola' if sys.stdin.isatty() else 'denegado, sin TTY'})"
        print(f"{_c('permisos', DIM)} {modo}")
        for nota in notas:
            print(f"{_c('nota', DIM)}   {nota}")
        extras = [n for n, on in (("plan", plan), ("memoria", memory)) if on]
        if extras:
            print(f"{_c('con', DIM)}    {', '.join(extras)}")
        print(f"{_c('tarea', DIM)}  {objective}")
        if save:
            print(f"{_c('run id', DIM)} {task.id}  {_c('(lfw9 resume <id> para retomar)', DIM)}")
        print()

        try:
            outcome = await harness.run(task)
        finally:
            await provider.aclose()

    print()
    color = GREEN if outcome.succeeded else RED
    print(_c(f"{RULE} {outcome.summary()}", color))
    if outcome.state_path:
        print(_c(f"   estados: {outcome.state_path}", DIM))
    if outcome.rejected_by:
        print(_c(f"   rechazos: {', '.join(outcome.rejected_by)}", DIM))
    print()
    if outcome.output:
        print(outcome.output)
    return 0 if outcome.succeeded else 2


async def cmd_resume(task_id: str, *, verbose: bool) -> int:
    store = FileCheckpointStore(settings.state_dir)
    snapshot = store.load(task_id)
    if snapshot is None:
        print(_c(f"{BAD_MARK} no hay ninguna corrida guardada con id '{task_id}'", RED))
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
        print(_c(f"{BAD_MARK} {exc}", RED))
        await provider.aclose()
        return 1

    harness = AgentHarness(
        provider,
        default_registry(),
        on_event=ConsoleSink(verbose),
        policy=default_policy(),
        approver=_build_approver(False),
        checkpoints=store,
    )
    print(f"{_c('repo', DIM)}   {snapshot.task.repo_path}")
    print(f"{_c('tarea', DIM)}  {snapshot.task.objective}")
    print(f"{_c('desde', DIM)}  turno {snapshot.turn} · {snapshot.state_path}")
    print()
    try:
        outcome = await harness.resume(task_id)
    finally:
        await provider.aclose()

    print()
    print(_c(f"{RULE} {outcome.summary()}", GREEN if outcome.succeeded else RED))
    print()
    if outcome.output:
        print(outcome.output)
    return 0 if outcome.succeeded else 2


async def cmd_eval(repo: str, *, verbose: bool, judge: bool = False) -> int:
    """Corre el dataset de golden tasks contra el LLM local."""
    repo_path = Path(repo).expanduser().resolve()
    if not repo_path.is_dir():
        print(_c(f"{BAD_MARK} '{repo}' no es un directorio", RED))
        return 1

    provider = build_provider()
    try:
        info = await provider.health()
    except ProviderError as exc:
        print(_c(f"{BAD_MARK} {exc}", RED))
        await provider.aclose()
        return 1

    suite = localforge_suite(str(repo_path))
    print(f"{_c('repo', DIM)}   {repo_path}")
    print(f"{_c('modelo', DIM)} {info['model']}")
    print(f"{_c('tareas', DIM)} {len(suite)}")

    # --judge: el juez puede ser otro modelo (LOCALFORGE_JUDGE_MODEL). Si es el
    # mismo, el reporte lo marca: un modelo juzgandose a si mismo es generoso.
    llm_judge = None
    judge_provider = None
    if judge:
        if settings.judge_model and settings.judge_model != info["model"]:
            judge_provider = build_provider(dataclasses.replace(settings, model=settings.judge_model))
            llm_judge = LLMJudge(judge_provider, agent_model=info["model"])
        else:
            llm_judge = LLMJudge(provider, agent_model=info["model"])
        print(f"{_c('juez', DIM)}   {getattr(llm_judge.provider, 'model', '?')}"
              + (_c("  (el mismo modelo que el agente)", YELLOW) if llm_judge.same_model else ""))
    print()

    def progreso(result) -> None:  # noqa: ANN001
        mark = _c(OK_MARK, GREEN) if result.passed else _c(BAD_MARK, RED)
        print(f"  {mark} {result.task_id} ({result.outcome.turns} turnos, {result.wall_seconds:.0f}s)")
        for check in result.failed_checks:
            print(f"      {_c('- ' + check.name + ': ' + check.detail, DIM)}")

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
        report = await run_suite(run_one, suite, label=info["model"], on_task=progreso, judge=llm_judge)
    finally:
        await provider.aclose()
        if judge_provider is not None:
            await judge_provider.aclose()

    print()
    print(report.render())
    return 0 if report.passed == report.total else 2


def cmd_submit(repo: str, objective: str, *, max_turns: int, plan: bool, memory: bool) -> int:
    repo_path = Path(repo).expanduser().resolve()
    if not repo_path.is_dir():
        print(_c(f"{BAD_MARK} '{repo}' no es un directorio", RED))
        return 1
    task = AgentTask(
        objective=objective,
        repo_path=str(repo_path),
        max_turns=max_turns,
        wall_clock_s=settings.wall_clock_s,
        token_budget=settings.token_budget,
        # Las opciones viajan con el job: el worker que lo tome puede ser otro
        # proceso, en otro momento.
        metadata={"plan": plan, "memory": memory},
    )
    job = FileQueue(settings.state_dir).submit(task)
    print(f"{_c(OK_MARK, GREEN)} encolado {job.id}")
    print(_c("  lo corre `lfw9 worker` (o `make worker WORLD=9`)", DIM))
    return 0


async def cmd_worker(*, once: bool, verbose: bool) -> int:
    provider = build_provider()
    try:
        info = await provider.health()
    except ProviderError as exc:
        print(_c(f"{BAD_MARK} {exc}", RED))
        await provider.aclose()
        return 1

    store = FileCheckpointStore(settings.state_dir)
    memory = FileMemoryStore(settings.state_dir)

    async def runner(job):  # noqa: ANN001, ANN202
        meta = job.task.metadata
        harness = AgentHarness(
            provider,
            default_registry(),
            on_event=ConsoleSink(verbose),
            # Un worker es desatendido: no hay humano para los ASK, asi que se
            # deniegan. Es la misma regla que en CI.
            policy=default_policy(),
            approver=DenyingApprover(),
            # Siempre con checkpoints: es lo que hace que un job recuperado de un
            # worker muerto RETOME en vez de empezar de cero.
            checkpoints=store,
            planner=Planner() if meta.get("plan") else None,
            memory=memory if meta.get("memory") else None,
        )
        return await run_or_resume(harness, job.task)

    def evento(event: str, **p: object) -> None:
        if event == "recovered":
            print(_c(f"recuperados (lease vencido): {', '.join(p['jobs'])}", YELLOW))  # type: ignore[arg-type]
        elif event == "claimed":
            job = p["job"]
            print(f"{RULE} job {job.id} (intento {job.attempts}): {job.task.objective[:70]}")  # type: ignore[attr-defined]
        elif event == "done":
            out = p["outcome"]
            # El job termino (por eso va a done/), pero el AGENTE puede haber
            # fallado: la marca tiene que decir lo segundo.
            ok = out.succeeded  # type: ignore[attr-defined]
            print(_c(f"{OK_MARK if ok else BAD_MARK} {out.summary()}", GREEN if ok else RED))  # type: ignore[attr-defined]
        elif event == "failed":
            print(_c(f"{BAD_MARK} el worker no pudo correrlo -> {p['state']}", RED))

    queue = FileQueue(settings.state_dir)
    worker = Worker(queue, runner, on_event=evento)
    print(f"{_c('worker', DIM)} {worker.id} · {info['model']} · cola en {queue.root}")
    try:
        if once:
            job = await worker.run_once()
            if job is None:
                print("no hay jobs pendientes")
        else:
            print(_c("esperando jobs (Ctrl+C para salir)", DIM))
            await worker.run_forever()
    except KeyboardInterrupt:
        pass
    finally:
        await provider.aclose()
    return 0


def cmd_jobs() -> int:
    queue = FileQueue(settings.state_dir)
    total = 0
    for state, jobs in queue.list().items():
        for job in jobs:
            total += 1
            extra = ""
            if job.outcome:
                extra = f" -> {job.outcome.get('status')}"
            elif job.last_error:
                extra = f" ({job.last_error[:60]})"
            print(f"  {state:8} {job.id}  intento {job.attempts}  {job.task.objective[:50]}{extra}")
    if not total:
        print("la cola esta vacia. `lfw9 submit <repo> \"<objetivo>\"` encola una tarea.")
    return 0


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
            print(f"  {task_id}  {_c(f'ilegible: {exc}', RED)}")
            continue
        if snap is None:
            continue
        print(f"  {task_id}")
        print(f"    {_c(snap.task.objective[:70], DIM)}")
        print(
            f"    turno {snap.turn} · {snap.status.value} · "
            f"{snap.tokens_in + snap.tokens_out} tok · tools: {len(snap.trajectory)}"
        )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="localforge", description="Coding agent local")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("health", help="Verifica que el LLM local responde")

    ask = sub.add_parser("ask", help="Corre el agente sobre un repositorio")
    ask.add_argument("repo", help="Ruta del repositorio")
    ask.add_argument("objective", help="Que queres que haga el agente")
    ask.add_argument("-v", "--verbose", action="store_true", help="Muestra el texto del modelo")
    ask.add_argument("--max-turns", type=int, default=settings.max_turns)
    ask.add_argument(
        "--read-only",
        action="store_true",
        help="Deniega toda tool con efectos sin preguntar. Para repos que no son tuyos.",
    )
    ask.add_argument(
        "--delegate",
        action="store_true",
        help="Habilita la tool `delegate`: el agente puede abrir subagentes con contexto propio.",
    )
    ask.add_argument(
        "--save",
        action="store_true",
        help="Guarda un checkpoint por turno para poder retomar con `resume`.",
    )
    ask.add_argument("--plan", action="store_true", help="El modelo escribe un plan antes de empezar (W3).")
    ask.add_argument(
        "--memory", action="store_true", help="Recuerda corridas verificadas de este repo (W6)."
    )
    ask.add_argument(
        "--sandbox",
        action="store_true",
        help="Habilita run_command dentro de un contenedor Docker sin red (W5). Sin Docker, no se habilita.",
    )
    ask.add_argument("--mcp", metavar="CONFIG", help="Archivo con servers MCP, formato mcpServers (W4).")
    ask.add_argument(
        "--edit",
        action="store_true",
        help="Habilita delegate_edit: un subagente edita en un git worktree y devuelve un diff (W8).",
    )

    subm = sub.add_parser("submit", help="Encola una tarea para que la corra un worker")
    subm.add_argument("repo")
    subm.add_argument("objective")
    subm.add_argument("--max-turns", type=int, default=settings.max_turns)
    subm.add_argument("--plan", action="store_true")
    subm.add_argument("--memory", action="store_true")

    wk = sub.add_parser("worker", help="Toma tareas de la cola y las corre (recupera las abandonadas)")
    wk.add_argument("--once", action="store_true", help="Corre un solo job y sale")
    wk.add_argument("-v", "--verbose", action="store_true")

    sub.add_parser("jobs", help="Lista la cola: pendientes, corriendo, terminados, fallados")

    res = sub.add_parser("resume", help="Retoma una corrida guardada")
    res.add_argument("task_id", help="Id de la corrida (lo lista `localforge runs`)")
    res.add_argument("-v", "--verbose", action="store_true")

    sub.add_parser("runs", help="Lista las corridas guardadas")

    ev = sub.add_parser("eval", help="Corre el dataset de golden tasks y reporta")
    ev.add_argument("repo", nargs="?", default=".", help="Repositorio sobre el que evaluar")
    ev.add_argument("-v", "--verbose", action="store_true")
    ev.add_argument(
        "--judge",
        action="store_true",
        help="Suma un juez LLM a las tareas con rubrica (LOCALFORGE_JUDGE_MODEL para usar otro modelo).",
    )

    args = parser.parse_args()

    if args.command == "health":
        return asyncio.run(cmd_health())
    if args.command == "ask":
        return asyncio.run(
            cmd_ask(
                args.repo,
                args.objective,
                verbose=args.verbose,
                max_turns=args.max_turns,
                read_only=args.read_only,
                delegate=args.delegate,
                save=args.save,
                plan=args.plan,
                memory=args.memory,
                sandbox=args.sandbox,
                mcp=args.mcp,
                edit=args.edit,
            )
        )
    if args.command == "submit":
        return cmd_submit(
            args.repo, args.objective, max_turns=args.max_turns, plan=args.plan, memory=args.memory
        )
    if args.command == "worker":
        return asyncio.run(cmd_worker(once=args.once, verbose=args.verbose))
    if args.command == "jobs":
        return cmd_jobs()
    if args.command == "resume":
        return asyncio.run(cmd_resume(args.task_id, verbose=args.verbose))
    if args.command == "runs":
        return cmd_runs()
    if args.command == "eval":
        return asyncio.run(cmd_eval(args.repo, verbose=args.verbose, judge=args.judge))
    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
