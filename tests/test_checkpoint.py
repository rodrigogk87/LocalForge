"""Tests de durabilidad (Fase 6).

La propiedad central es una sola: **matar el proceso a mitad de camino y
retomar tiene que dar el mismo resultado que no haberlo matado.** El resto de
los tests son las formas concretas en que eso se rompe.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from localforge.config import Settings
from localforge.harness import AgentHarness
from localforge.durable import (
    CHECKPOINT_VERSION,
    Checkpoint,
    FileCheckpointStore,
    IncompatibleCheckpoint,
    MemoryCheckpointStore,
)
from localforge.models import (
    AgentMessage,
    AgentStatus,
    AgentTask,
    ModelResponse,
    StopReason,
    ToolCall,
)
from localforge.tools import default_registry


class Scripted:
    """Provider que puede morirse a mitad de camino, como la vida real."""

    name = model = "scripted"

    def __init__(self, responses: list[ModelResponse], die_after: int | None = None) -> None:
        self.responses = list(responses)
        self.calls = 0
        self.die_after = die_after

    async def complete(self, messages, tools=None, *, system=None, max_tokens=None):  # noqa: ANN001
        if self.die_after is not None and self.calls >= self.die_after:
            raise KeyboardInterrupt("el proceso se murio")
        self.calls += 1
        if not self.responses:
            return ModelResponse(content="sin mas", stop_reason=StopReason.END_TURN)
        return self.responses.pop(0)

    async def health(self):  # noqa: ANN201
        return {}

    async def aclose(self) -> None:
        return None


def text(c: str) -> ModelResponse:
    return ModelResponse(content=c, stop_reason=StopReason.END_TURN, input_tokens=100, output_tokens=20)


def call(name: str, **args) -> ModelResponse:
    return ModelResponse(
        tool_calls=[ToolCall(id=f"c_{name}_{len(args)}", name=name, arguments=args)],
        stop_reason=StopReason.TOOL_USE,
        input_tokens=100,
        output_tokens=20,
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    d = tmp_path / "repo"
    d.mkdir()
    for i in range(4):
        (d / f"f{i}.py").write_text(f"def f{i}():\n    return {i}\n", encoding="utf-8")
    return d


def task_for(repo: Path, **kw) -> AgentTask:
    return AgentTask(objective="explicame el repo", repo_path=str(repo), max_turns=12, **kw)


def harness(provider, store, **kw):  # noqa: ANN001, ANN201
    return AgentHarness(
        provider, default_registry(), cfg=Settings(), checkpoints=store, **kw
    )


# --- la propiedad central ---------------------------------------------------


async def test_matar_y_retomar_da_el_mismo_resultado(repo: Path) -> None:
    store = MemoryCheckpointStore()
    guion = [
        call("read_file", path="f0.py"),
        call("read_file", path="f1.py"),
        call("read_file", path="f2.py"),
        text("f0, f1 y f2 definen funciones que devuelven su indice"),
    ]

    # Primera corrida: se muere despues de dos llamadas al modelo.
    muerto = Scripted(list(guion), die_after=2)
    task = task_for(repo)
    with pytest.raises(KeyboardInterrupt):
        await harness(muerto, store).run(task)

    # El checkpoint sobrevivio y sabe donde estaba.
    snap = store.load(task.id)
    assert snap is not None
    assert snap.turn == 2
    assert snap.trajectory == ["read_file", "read_file"]

    # Retomar: el provider arranca con lo que faltaba del guion.
    resto = Scripted(guion[2:])
    out = await harness(resto, store).resume(str(task.id))

    assert out.status is AgentStatus.COMPLETED
    # La trayectoria completa, incluyendo lo que paso ANTES del crash.
    assert out.trajectory == ["read_file", "read_file", "read_file"]
    # Y los tokens de los dos turnos perdidos siguen contados: si no, el
    # presupuesto se reiniciaria en cada crash y no seria un presupuesto.
    assert out.input_tokens >= 300


async def test_resumir_no_reejecuta_lo_ya_hecho(repo: Path) -> None:
    store = MemoryCheckpointStore()
    task = task_for(repo)
    muerto = Scripted([call("read_file", path="f0.py"), call("read_file", path="f1.py")], die_after=2)
    with pytest.raises(KeyboardInterrupt):
        await harness(muerto, store).run(task)

    resto = Scripted([text("f0 y f1 devuelven 0 y 1")])
    out = await harness(resto, store).resume(str(task.id))

    # Exactamente dos read_file, no cuatro.
    assert out.trajectory.count("read_file") == 2
    assert resto.calls == 1  # el provider solo se llamo para la respuesta final


# --- lo que se persiste -----------------------------------------------------


async def test_el_contador_de_loops_sobrevive(repo: Path) -> None:
    """Sin esto, un agente en loop giraria para siempre cruzando reinicios."""
    store = MemoryCheckpointStore()
    task = task_for(repo)
    repetida = [call("read_file", path="f0.py")] * 2
    muerto = Scripted(repetida, die_after=2)
    with pytest.raises(KeyboardInterrupt):
        await harness(muerto, store).run(task)

    snap = store.load(task.id)
    assert snap is not None
    assert sum(snap.seen.values()) == 2


async def test_la_calibracion_del_estimador_sobrevive(repo: Path) -> None:
    store = MemoryCheckpointStore()
    task = task_for(repo)
    muerto = Scripted([call("read_file", path="f0.py")], die_after=1)
    with pytest.raises(KeyboardInterrupt):
        await harness(muerto, store).run(task)
    snap = store.load(task.id)
    assert snap is not None and snap.chars_per_token is not None


async def test_el_camino_de_estados_sobrevive(repo: Path) -> None:
    store = MemoryCheckpointStore()
    task = task_for(repo)
    muerto = Scripted([call("read_file", path="f0.py")], die_after=1)
    with pytest.raises(KeyboardInterrupt):
        await harness(muerto, store).run(task)
    snap = store.load(task.id)
    assert snap is not None and "waiting_tool" in snap.state_path


async def test_se_graba_un_checkpoint_por_turno(repo: Path) -> None:
    store = MemoryCheckpointStore()
    provider = Scripted([call("read_file", path=f"f{i}.py") for i in range(3)] + [text("listo")])
    await harness(provider, store).run(task_for(repo))
    assert store.writes == 3  # uno por turno con tools; el final no necesita


# --- sin store, nada cambia -------------------------------------------------


async def test_sin_store_el_agente_funciona_igual(repo: Path) -> None:
    provider = Scripted([call("read_file", path="f0.py"), text("f0 devuelve 0")])
    out = await AgentHarness(provider, default_registry(), cfg=Settings()).run(task_for(repo))
    assert out.status is AgentStatus.COMPLETED


async def test_resumir_sin_store_es_un_error_claro(repo: Path) -> None:
    provider = Scripted([])
    h = AgentHarness(provider, default_registry(), cfg=Settings())
    with pytest.raises(RuntimeError, match="no hay checkpoint store"):
        await h.resume("cualquiera")


async def test_resumir_algo_que_no_existe_es_un_error_claro() -> None:
    h = AgentHarness(Scripted([]), default_registry(), cfg=Settings(), checkpoints=MemoryCheckpointStore())
    with pytest.raises(FileNotFoundError):
        await h.resume("no-existe")


# --- FileCheckpointStore ----------------------------------------------------


def snapshot_for(tmp_path: Path, **kw) -> Checkpoint:
    return Checkpoint(
        task=AgentTask(objective="x", repo_path=str(tmp_path)),
        turn=1,
        messages=[AgentMessage(role="user", content="hola")],
        **kw,
    )


def test_ida_y_vuelta_por_disco(tmp_path: Path) -> None:
    store = FileCheckpointStore(tmp_path / ".localforge")
    snap = snapshot_for(tmp_path, tokens_in=42, trajectory=["read_file"])
    store.save(snap)

    leido = store.load(snap.task_id)
    assert leido is not None
    assert leido.tokens_in == 42
    assert leido.trajectory == ["read_file"]
    assert leido.messages[0].content == "hola"


def test_load_de_algo_que_no_existe_da_none(tmp_path: Path) -> None:
    assert FileCheckpointStore(tmp_path).load("nada") is None


def test_list_ids(tmp_path: Path) -> None:
    store = FileCheckpointStore(tmp_path / ".localforge")
    a, b = snapshot_for(tmp_path), snapshot_for(tmp_path)
    store.save(a)
    store.save(b)
    assert sorted(store.list_ids()) == sorted([str(a.task_id), str(b.task_id)])


def test_sobrescribir_no_deja_temporales(tmp_path: Path) -> None:
    root = tmp_path / ".localforge"
    store = FileCheckpointStore(root)
    snap = snapshot_for(tmp_path)
    for i in range(5):
        store.save(snap.model_copy(update={"turn": i}))
    sobrantes = list((root / "runs").glob(".tmp-*"))
    assert sobrantes == [], sobrantes
    assert store.load(snap.task_id).turn == 4


def test_una_version_distinta_se_rechaza_explicito(tmp_path: Path) -> None:
    """Un campo renombrado no puede ser un crash confuso mucho mas adelante."""
    store = FileCheckpointStore(tmp_path / ".localforge")
    snap = snapshot_for(tmp_path)
    store.save(snap)

    path = (tmp_path / ".localforge" / "runs" / f"{snap.task_id}.json")
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["version"] = CHECKPOINT_VERSION + 99
    path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(IncompatibleCheckpoint) as exc:
        store.load(snap.task_id)
    assert "version" in str(exc.value)


def test_un_json_corrupto_se_rechaza_explicito(tmp_path: Path) -> None:
    store = FileCheckpointStore(tmp_path / ".localforge")
    snap = snapshot_for(tmp_path)
    store.save(snap)
    path = tmp_path / ".localforge" / "runs" / f"{snap.task_id}.json"
    path.write_text("{ esto no es json", encoding="utf-8")

    with pytest.raises(IncompatibleCheckpoint):
        store.load(snap.task_id)
