"""Queue, worker y recovery automatico (W6-C40, lo que el mundo dejo pendiente).

Lo que se prueba es lo que la cola garantiza: que un job lo toma UN worker, que
el job de un worker muerto vuelve solo, y que al volver retoma desde su
checkpoint en vez de repetir el trabajo.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from localforge.config import Settings
from localforge.harness import AgentHarness
from localforge.harness.checkpoint import FileCheckpointStore
from localforge.harness.queue import MAX_ATTEMPTS, FileQueue, Worker, run_or_resume
from localforge.models import AgentTask, ModelResponse, StopReason, ToolCall
from localforge.tools import default_registry


class Scripted:
    name = model = "scripted"

    def __init__(self, responses: list[ModelResponse]) -> None:
        self.responses = list(responses)
        self.calls = 0

    async def complete(self, messages, tools=None, *, system=None, max_tokens=None):  # noqa: ANN001
        self.calls += 1
        return self.responses.pop(0)

    async def health(self):  # noqa: ANN201
        return {}

    async def aclose(self) -> None:
        return None


def text(c: str) -> ModelResponse:
    return ModelResponse(content=c, stop_reason=StopReason.END_TURN, input_tokens=10)


def read(path: str, i: int = 0) -> ModelResponse:
    return ModelResponse(
        tool_calls=[ToolCall(id=f"r{i}", name="read_file", arguments={"path": path, "offset": i})],
        stop_reason=StopReason.TOOL_USE,
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    r.mkdir()
    (r / "app.py").write_text("def main():\n    return 1\n", encoding="utf-8")
    return r


def task(repo: Path, **kw) -> AgentTask:
    return AgentTask(objective="que hace main", repo_path=str(repo), **kw)


# --- la cola ----------------------------------------------------------------------


def test_un_job_lo_toma_un_solo_worker(tmp_path: Path, repo: Path) -> None:
    q = FileQueue(tmp_path)
    q.submit(task(repo))
    assert q.claim("w1") is not None
    assert q.claim("w2") is None, "dos workers tomaron el mismo job"


def test_se_toma_el_mas_viejo_primero(tmp_path: Path, repo: Path) -> None:
    q = FileQueue(tmp_path)
    a = q.submit(task(repo))
    os.utime(q._path("pending", a.id), (time.time() - 100,) * 2)
    q.submit(task(repo))
    assert q.claim("w").id == a.id


def test_un_lease_vencido_vuelve_a_pending(tmp_path: Path, repo: Path) -> None:
    q = FileQueue(tmp_path, lease_s=10)
    job = q.submit(task(repo))
    q.claim("muerto")
    assert q.recover() == [], "recupero un job con lease vigente"
    assert q.recover(now=time.time() + 11) == [job.id]
    vuelto = q.list()["pending"][0]
    assert "lease vencido" in vuelto.last_error and "muerto" in vuelto.last_error


def test_un_job_que_falla_siempre_termina_apartado(tmp_path: Path, repo: Path) -> None:
    q = FileQueue(tmp_path)
    q.submit(task(repo))
    for intento in range(1, MAX_ATTEMPTS + 1):
        job = q.claim("w")
        estado = q.fail(job, "boom")
        assert estado == ("failed" if intento == MAX_ATTEMPTS else "pending")
    assert q.claim("w") is None
    assert len(q.list()["failed"]) == 1


# --- el worker ---------------------------------------------------------------------


async def test_el_worker_corre_el_job_y_lo_marca_done(tmp_path: Path, repo: Path) -> None:
    q = FileQueue(tmp_path)
    q.submit(task(repo))
    provider = Scripted([read("app.py"), text("app.py:1 define main.")])

    async def runner(job):  # noqa: ANN001, ANN202
        return await AgentHarness(provider, default_registry(), cfg=Settings()).run(job.task)

    job = await Worker(q, runner).run_once()
    assert job is not None
    done = q.list()["done"]
    assert len(done) == 1 and done[0].outcome["status"] == "completed"
    assert not list((q.root / "running").iterdir()), "quedo basura en running/"
    assert await Worker(q, runner).run_once() is None


async def test_un_outcome_fallido_es_un_job_terminado(tmp_path: Path, repo: Path) -> None:
    """max_turns es un final legitimo del AGENTE, no un fallo del WORKER."""
    q = FileQueue(tmp_path)
    q.submit(task(repo, max_turns=1))

    async def runner(job):  # noqa: ANN001, ANN202
        return await AgentHarness(Scripted([read("app.py")]), default_registry(), cfg=Settings()).run(job.task)

    await Worker(q, runner).run_once()
    assert q.list()["done"][0].outcome["reason"] == "max_turns"


async def test_una_excepcion_del_runner_no_mata_al_worker(tmp_path: Path, repo: Path) -> None:
    q = FileQueue(tmp_path)
    q.submit(task(repo))

    async def runner(job):  # noqa: ANN001, ANN202
        raise RuntimeError("se cayo ollama")

    await Worker(q, runner).run_once()
    pendiente = q.list()["pending"][0]
    assert "se cayo ollama" in pendiente.last_error and pendiente.attempts == 1


async def test_recovery_retoma_desde_el_checkpoint_sin_repetir(tmp_path: Path, repo: Path) -> None:
    """El escenario completo: un worker muere a mitad de la corrida, otro la
    recupera por lease vencido y SIGUE desde el ultimo turno grabado."""
    q = FileQueue(tmp_path, lease_s=5)
    store = FileCheckpointStore(tmp_path)
    t = q.submit(task(repo, max_turns=10)).task

    # Worker 1: toma el job, corre dos turnos y "muere" en el tercero. Un
    # BaseException imita un kill: nada lo atrapa, no completa ni libera.
    class Muerte(BaseException):
        pass

    class MuereEnElTercero(Scripted):
        async def complete(self, *a, **k):  # noqa: ANN002, ANN003, ANN202
            if not self.responses:
                raise Muerte
            return await super().complete(*a, **k)

    job = q.claim("w1")
    h1 = AgentHarness(
        MuereEnElTercero([read("app.py", 0), read("app.py", 1)]),
        default_registry(), cfg=Settings(), checkpoints=store,
    )
    with pytest.raises(Muerte):
        await h1.run(job.task)
    assert store.load(t.id).turn == 2

    # Pasa el lease. Worker 2 arranca, recupera y retoma.
    os.utime(q._lease(job.id), (time.time() - 10,) * 2)
    p2 = Scripted([text("app.py:1 define main.")])

    async def runner(j):  # noqa: ANN001, ANN202
        h = AgentHarness(p2, default_registry(), cfg=Settings(), checkpoints=store)
        return await run_or_resume(h, j.task)

    eventos: list[str] = []
    await Worker(q, runner, on_event=lambda e, **_: eventos.append(e)).run_once()
    assert eventos[:2] == ["recovered", "claimed"]
    assert p2.calls == 1, "repitio turnos que ya estaban en el checkpoint"
    done = q.list()["done"][0]
    assert done.outcome["status"] == "completed" and done.attempts == 2
    assert done.outcome["trajectory"] == ["read_file", "read_file"]
