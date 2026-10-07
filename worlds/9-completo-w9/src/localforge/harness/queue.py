"""Queue y worker (W6-C40): la corrida deja de depender de una terminal abierta.

Con checkpoints, una corrida PUEDE sobrevivir a un crash. Pero alguien tiene que
darse cuenta de que crasheo y correr `resume`. Este modulo es ese alguien:

    submit  ->  pending/   ->  (un worker la toma)  ->  running/  ->  done/
                    ^                                       |          failed/
                    └──── recover(): lease vencido ─────────┘

Es una cola **en el filesystem**, sin Redis ni broker, y las garantias salen de
dos primitivas del sistema operativo:

1. **Tomar un job es un `os.rename`.** De `pending/x.json` a `running/x.json`.
   El rename es atomico: si dos workers intentan tomar el mismo job, uno gana y
   el otro recibe FileNotFoundError. No hace falta un lock: el filesystem ya es
   el arbitro.

2. **El lease es el mtime de un archivo.** El worker que tiene un job lo
   "toca" cada pocos segundos. Si el proceso muere, deja de tocarlo; pasado el
   lease, cualquier worker lo devuelve a `pending/`. Eso es el **recovery
   automatico** que faltaba: nadie corre `resume` a mano.

Y el recovery no repite trabajo por la propiedad que los checkpoints ya tenian:
el worker que retoma un job encuentra su checkpoint y **resume** en vez de
arrancar de cero. Resumir es idempotente porque el checkpoint se graba despues
de aplicar los resultados de las tools.

Lo que NO es: durable execution estilo Temporal (W6-C41). No hay replay
determinista de cada paso ni historial de eventos; la unidad de durabilidad es
el turno, no la llamada. Para un agente cuyas tools no tienen efectos sobre el
repo, alcanza.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable
from uuid import uuid4

from pydantic import BaseModel, Field

from localforge.models import AgentOutcome, AgentTask

STATES = ("pending", "running", "done", "failed")

# Cuanto puede pasar sin heartbeat antes de dar un job por abandonado. Tiene que
# ser bastante mas que el intervalo de heartbeat: un worker lento no es un
# worker muerto, y robarle el job a uno vivo seria correr la tarea dos veces.
DEFAULT_LEASE_S = 60.0

# Despues de esto, un job que sigue fallando se aparta. Reintentar para siempre
# una tarea que rompe el worker es convertir un bug en un loop infinito.
MAX_ATTEMPTS = 3


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Job(BaseModel):
    id: str
    task: AgentTask
    attempts: int = 0
    enqueued_at: datetime = Field(default_factory=_utcnow)
    worker: str = ""
    last_error: str = ""
    # Se llena al terminar. Un outcome FAILED (max_turns, verification_failed)
    # es un job DONE: el agente termino de forma legitima. `failed/` es para
    # cuando el WORKER no pudo correrlo.
    outcome: dict | None = None


def _atomic_write(path: Path, text: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


class FileQueue:
    def __init__(self, root: Path | str = ".localforge", *, lease_s: float = DEFAULT_LEASE_S) -> None:
        self.root = Path(root) / "queue"
        self.lease_s = lease_s
        for state in STATES:
            (self.root / state).mkdir(parents=True, exist_ok=True)

    def _path(self, state: str, job_id: str) -> Path:
        return self.root / state / f"{job_id}.json"

    def _lease(self, job_id: str) -> Path:
        return self.root / "running" / f"{job_id}.lease"

    def _write(self, state: str, job: Job) -> None:
        _atomic_write(self._path(state, job.id), job.model_dump_json(indent=2))

    def _read(self, path: Path) -> Job:
        return Job.model_validate_json(path.read_text(encoding="utf-8"))

    # -- productor ------------------------------------------------------------

    def submit(self, task: AgentTask) -> Job:
        job = Job(id=str(task.id), task=task)
        self._write("pending", job)
        return job

    # -- worker ---------------------------------------------------------------

    def claim(self, worker: str) -> Job | None:
        """Toma el job pendiente mas viejo. Atomico por `os.rename`."""
        pending = sorted(
            (self.root / "pending").glob("*.json"), key=lambda p: (p.stat().st_mtime, p.name)
        )
        for path in pending:
            target = self.root / "running" / path.name
            try:
                os.rename(path, target)
            except FileNotFoundError:
                continue  # otro worker lo tomo primero: es el caso normal, no un error
            job = self._read(target)
            job.worker = worker
            job.attempts += 1
            self._write("running", job)
            self.heartbeat(job)
            return job
        return None

    def heartbeat(self, job: Job) -> None:
        lease = self._lease(job.id)
        lease.write_text(job.worker, encoding="utf-8")
        os.utime(lease, None)

    def complete(self, job: Job, outcome: AgentOutcome) -> None:
        job.outcome = json.loads(outcome.model_dump_json(exclude={"turn_records"}))
        self._write("done", job)
        self._release(job)

    def fail(self, job: Job, error: str, *, max_attempts: int = MAX_ATTEMPTS) -> str:
        """Devuelve el job a la cola o lo aparta. Devuelve el estado final."""
        job.last_error = error
        state = "pending" if job.attempts < max_attempts else "failed"
        self._write(state, job)
        self._release(job)
        return state

    def _release(self, job: Job) -> None:
        self._path("running", job.id).unlink(missing_ok=True)
        self._lease(job.id).unlink(missing_ok=True)

    # -- recovery -------------------------------------------------------------

    def recover(self, *, now: float | None = None) -> list[str]:
        """Devuelve a `pending/` los jobs cuyo worker dejo de dar señales.

        Se llama al arrancar cada worker y antes de cada claim: no hace falta un
        proceso supervisor aparte, cualquier worker vivo limpia lo de los muertos.
        """
        now = time.time() if now is None else now
        recovered: list[str] = []
        for path in (self.root / "running").glob("*.json"):
            lease = self._lease(path.stem)
            last = lease.stat().st_mtime if lease.exists() else path.stat().st_mtime
            if now - last < self.lease_s:
                continue
            target = self._path("pending", path.stem)
            try:
                os.rename(path, target)  # mismo truco: si otro lo recupero, perdemos
            except FileNotFoundError:
                continue
            lease.unlink(missing_ok=True)
            job = self._read(target)
            job.last_error = f"lease vencido (worker '{job.worker}' sin señales por {now - last:.0f}s)"
            self._write("pending", job)
            recovered.append(job.id)
        return recovered

    # -- lectura --------------------------------------------------------------

    def list(self) -> dict[str, list[Job]]:
        out: dict[str, list[Job]] = {}
        for state in STATES:
            jobs = []
            for p in sorted((self.root / state).glob("*.json")):
                try:
                    jobs.append(self._read(p))
                except (OSError, ValueError):
                    continue
            out[state] = jobs
        return out


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------

# Recibe el job y devuelve el outcome. Lo arma quien conoce el provider y las
# politicas (la CLI); el worker solo sabe de colas.
Runner = Callable[[Job], Awaitable[AgentOutcome]]


async def run_or_resume(harness, task: AgentTask) -> AgentOutcome:  # noqa: ANN001 - AgentHarness
    """Si el job ya tiene checkpoint, retoma; si no, arranca.

    Es la mitad del recovery que no esta en la cola: un job recuperado de un
    worker muerto NO vuelve a empezar, sigue desde el ultimo turno grabado.
    """
    store = harness.checkpoints
    if store is not None and store.load(task.id) is not None:
        return await harness.resume(str(task.id))
    return await harness.run(task)


def default_worker_id() -> str:
    return f"{socket.gethostname()}-{os.getpid()}-{uuid4().hex[:4]}"


class Worker:
    def __init__(
        self,
        queue: FileQueue,
        runner: Runner,
        *,
        worker_id: str | None = None,
        on_event: Callable[..., None] | None = None,
    ) -> None:
        self.queue = queue
        self.runner = runner
        self.id = worker_id or default_worker_id()
        self.on_event = on_event or (lambda *_a, **_k: None)

    async def run_once(self) -> Job | None:
        """Recupera abandonados, toma un job y lo corre. None si no habia nada."""
        recovered = self.queue.recover()
        if recovered:
            self.on_event("recovered", jobs=recovered)
        job = self.queue.claim(self.id)
        if job is None:
            return None
        self.on_event("claimed", job=job)

        async def beat() -> None:
            while True:
                await asyncio.sleep(self.queue.lease_s / 4)
                self.queue.heartbeat(job)

        beating = asyncio.create_task(beat())
        try:
            outcome = await self.runner(job)
        except Exception as exc:  # noqa: BLE001 - un job roto no mata al worker
            state = self.queue.fail(job, f"{type(exc).__name__}: {exc}")
            self.on_event("failed", job=job, state=state)
            return job
        finally:
            beating.cancel()
        self.queue.complete(job, outcome)
        self.on_event("done", job=job, outcome=outcome)
        return job

    async def run_forever(self, *, poll_s: float = 2.0, stop: asyncio.Event | None = None) -> None:
        stop = stop or asyncio.Event()
        while not stop.is_set():
            job = await self.run_once()
            if job is None:
                try:
                    await asyncio.wait_for(stop.wait(), timeout=poll_s)
                except asyncio.TimeoutError:
                    pass


__all__ = ["FileQueue", "Job", "Worker", "Runner", "run_or_resume", "MAX_ATTEMPTS", "DEFAULT_LEASE_S", "default_worker_id"]
