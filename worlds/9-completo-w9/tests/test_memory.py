"""Memoria entre sesiones (W6, lo que el mundo dejo pendiente).

Las propiedades que importan son de seguridad: solo se recuerda lo verificado,
lo recordado no cuenta como evidencia, y vive fuera del repo analizado.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from localforge.config import Settings
from localforge.harness import AgentHarness
from localforge.harness.memory import FileMemoryStore, InMemoryStore, render_block
from localforge.models import AgentStatus, AgentTask, ModelResponse, StopReason, ToolCall
from localforge.tools import default_registry


class Scripted:
    name = model = "scripted"

    def __init__(self, responses: list[ModelResponse]) -> None:
        self.responses = list(responses)
        self.systems: list[str | None] = []

    async def complete(self, messages, tools=None, *, system=None, max_tokens=None):  # noqa: ANN001
        self.systems.append(system)
        return self.responses.pop(0)

    async def health(self):  # noqa: ANN201
        return {}

    async def aclose(self) -> None:
        return None


def text(c: str) -> ModelResponse:
    return ModelResponse(content=c, stop_reason=StopReason.END_TURN, input_tokens=10)


def read(path: str) -> ModelResponse:
    return ModelResponse(
        tool_calls=[ToolCall(id=f"r_{path}", name="read_file", arguments={"path": path})],
        stop_reason=StopReason.TOOL_USE,
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    r.mkdir()
    (r / "fs.py").write_text("def safe_path(root, p):\n    return root / p\n", encoding="utf-8")
    return r


async def run(provider, repo: Path, memory, objective: str = "donde se valida la ruta"):  # noqa: ANN001
    h = AgentHarness(provider, default_registry(), cfg=Settings(), memory=memory)
    return await h.run(AgentTask(objective=objective, repo_path=str(repo)))


async def test_una_corrida_verificada_se_recuerda_en_la_siguiente(repo: Path) -> None:
    mem = InMemoryStore()
    await run(Scripted([read("fs.py"), text("Se valida en fs.py:1, safe_path.")]), repo, mem)

    p2 = Scripted([read("fs.py"), text("fs.py:1.")])
    await run(p2, repo, mem, objective="donde se valida que la ruta no escape")
    system = p2.systems[0] or ""
    assert "MEMORIA DE CORRIDAS ANTERIORES" in system
    assert "fs.py:1, safe_path" in system
    assert "leyo: fs.py" in system


async def test_una_respuesta_rechazada_no_se_recuerda(repo: Path) -> None:
    """El verifier rechaza dos veces y se agotan las reparaciones."""
    mem = InMemoryStore()
    p = Scripted([text("posiblemente en fs.py")] * 3)
    h = AgentHarness(p, default_registry(), cfg=Settings(), memory=mem, max_repairs=2)
    out = await h.run(AgentTask(objective="donde se valida la ruta", repo_path=str(repo)))
    assert out.status is AgentStatus.FAILED
    assert mem.recall(repo, "donde se valida la ruta") == []


async def test_la_memoria_no_cuenta_como_evidencia(repo: Path) -> None:
    """Con la respuesta en memoria, contestar sin leer igual se rechaza."""
    mem = InMemoryStore()
    await run(Scripted([read("fs.py"), text("fs.py:1, safe_path.")]), repo, mem)
    p2 = Scripted([text("fs.py:1, safe_path."), read("fs.py"), text("fs.py:1, safe_path.")])
    out = await run(p2, repo, mem)
    assert out.rejected_by == ["trayectoria"], "la memoria se acepto como evidencia"
    assert out.status is AgentStatus.COMPLETED


async def test_sin_relacion_con_la_pregunta_no_se_inyecta_nada(repo: Path) -> None:
    mem = InMemoryStore()
    await run(Scripted([read("fs.py"), text("fs.py:1, safe_path.")]), repo, mem)
    p2 = Scripted([read("fs.py"), text("ok fs.py:1")])
    await run(p2, repo, mem, objective="cuantos tests hay en el ci")
    assert "MEMORIA" not in (p2.systems[0] or "")


async def test_el_archivo_vive_fuera_del_repo_y_es_por_repo(tmp_path: Path, repo: Path) -> None:
    state = tmp_path / "state"
    store = FileMemoryStore(state)
    await run(Scripted([read("fs.py"), text("fs.py:1, safe_path.")]), repo, store)
    assert not any(p.suffix == ".jsonl" for p in repo.rglob("*")), "escribio dentro del repo analizado"
    assert store.path_for(repo).parent == state / "memory"
    assert len(store.load(repo)) == 1

    otro = tmp_path / "otro"
    otro.mkdir()
    assert store.recall(otro, "donde se valida la ruta") == []


def test_una_linea_corrupta_no_borra_la_memoria(tmp_path: Path, repo: Path) -> None:
    store = FileMemoryStore(tmp_path)
    path = store.path_for(repo)
    path.parent.mkdir(parents=True)
    path.write_text(
        '{"task_id":"1","objective":"ruta","answer":"fs.py:1"}\nesto no es json\n', encoding="utf-8"
    )
    assert [e.answer for e in store.load(repo)] == ["fs.py:1"]


def test_el_bloque_avisa_que_no_es_evidencia() -> None:
    from localforge.harness.memory import MemoryEntry

    block = render_block([MemoryEntry(task_id="1", objective="q", answer="r")])
    assert "NO son evidencia" in block
    assert render_block([]) == ""


async def test_la_capa_memory_se_mide(repo: Path) -> None:
    mem = InMemoryStore()
    await run(Scripted([read("fs.py"), text("fs.py:1, safe_path.")]), repo, mem)
    capas = []
    h = AgentHarness(
        Scripted([read("fs.py"), text("fs.py:1")]),
        default_registry(),
        cfg=Settings(),
        memory=mem,
        on_event=lambda e, **p: capas.append(p["breakdown"]) if e == "context_built" else None,
    )
    await h.run(AgentTask(objective="donde se valida la ruta", repo_path=str(repo)))
    layer = capas[0].layer("memory")
    assert layer.present and layer.tokens > 0
