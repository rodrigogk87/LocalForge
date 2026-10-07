"""Planner (W3, lo que el mundo dejo pendiente).

Propiedades: el plan se pide SIN tools, entra a la tarea, pasa por el estado
PLANNING solo si hay planner, y un planner que falla no mata la corrida.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from localforge.config import Settings
from localforge.harness import AgentHarness
from localforge.harness.checkpoint import MemoryCheckpointStore
from localforge.harness.planner import MAX_STEPS, Planner, parse_plan
from localforge.harness.state import StateMachine
from localforge.models import AgentStatus, AgentTask, ModelResponse, StopReason, ToolCall
from localforge.providers.base import ProviderError
from localforge.tools import default_registry


class Scripted:
    name = model = "scripted"

    def __init__(self, responses: list[ModelResponse | Exception]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[list, object]] = []

    async def complete(self, messages, tools=None, *, system=None, max_tokens=None):  # noqa: ANN001
        self.calls.append((list(messages), tools))
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    async def health(self):  # noqa: ANN201
        return {}

    async def aclose(self) -> None:
        return None


PLAN = "Aqui esta el plan:\n1. Buscar donde se define main\n2. **Leer** ese archivo\n3. Responder citando linea"


def text(c: str) -> ModelResponse:
    return ModelResponse(content=c, stop_reason=StopReason.END_TURN, input_tokens=30, output_tokens=10)


def read(path: str) -> ModelResponse:
    return ModelResponse(
        tool_calls=[ToolCall(id="r1", name="read_file", arguments={"path": path})],
        stop_reason=StopReason.TOOL_USE,
        input_tokens=30,
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / "app.py").write_text("def main():\n    return 1\n", encoding="utf-8")
    return tmp_path


def harness(provider, **kw) -> AgentHarness:  # noqa: ANN001
    return AgentHarness(provider, default_registry(), cfg=Settings(), **kw)


# --- parseo ---------------------------------------------------------------------


def test_se_quedan_solo_las_lineas_numeradas() -> None:
    plan = parse_plan(PLAN)
    assert plan.steps == ["Buscar donde se define main", "Leer ese archivo", "Responder citando linea"]


def test_un_plan_demasiado_largo_se_corta() -> None:
    texto = "\n".join(f"{i}. paso {i}" for i in range(1, 20))
    assert len(parse_plan(texto).steps) == MAX_STEPS


def test_sin_lista_no_hay_plan() -> None:
    assert not parse_plan("No se me ocurre ningun plan.")


# --- estados ----------------------------------------------------------------------


def test_planning_es_opcional_en_el_grafo() -> None:
    assert StateMachine().can(AgentStatus.PLANNING)
    assert StateMachine().can(AgentStatus.RUNNING)
    m = StateMachine()
    m.to(AgentStatus.PLANNING)
    # Desde PLANNING no se puede saltar a verificar: hay que trabajar primero.
    assert not m.can(AgentStatus.VERIFYING)
    assert m.can(AgentStatus.RUNNING)


# --- integracion con el loop -----------------------------------------------------


async def test_con_planner_pasa_por_planning_y_el_plan_entra_a_la_tarea(repo: Path) -> None:
    provider = Scripted([text(PLAN), read("app.py"), text("app.py:1 define main().")])
    out = await harness(provider, planner=Planner()).run(
        AgentTask(objective="que hace main", repo_path=str(repo))
    )
    assert out.status is AgentStatus.COMPLETED
    assert out.state_path.startswith("created -> planning -> running")
    assert out.plan[0] == "Buscar donde se define main"
    # La llamada del plan va SIN tools: planear es decidir que mirar, no mirarlo.
    assert provider.calls[0][1] is None
    # El primer turno de trabajo ve la tarea con el plan adentro.
    primer_turno = provider.calls[1][0]
    assert "PLAN" in primer_turno[0].content and "1. Buscar donde se define main" in primer_turno[0].content
    # Y los tokens del plan se cuentan en el presupuesto.
    assert out.input_tokens == 90


async def test_sin_planner_no_hay_estado_planning(repo: Path) -> None:
    provider = Scripted([read("app.py"), text("app.py:1 define main().")])
    out = await harness(provider).run(AgentTask(objective="que hace main", repo_path=str(repo)))
    assert "planning" not in out.state_path
    assert out.plan == []


async def test_un_planner_que_falla_no_mata_la_corrida(repo: Path) -> None:
    eventos: list[str] = []
    provider = Scripted([ProviderError("se cayo"), read("app.py"), text("app.py:1 define main().")])
    out = await harness(
        provider, planner=Planner(), on_event=lambda e, **_: eventos.append(e)
    ).run(AgentTask(objective="que hace main", repo_path=str(repo)))
    assert out.status is AgentStatus.COMPLETED
    assert out.plan == []
    assert "plan_failed" in eventos
    assert "planning -> running" in out.state_path


async def test_al_resumir_no_se_replanea(repo: Path) -> None:
    store = MemoryCheckpointStore()
    task = AgentTask(objective="que hace main", repo_path=str(repo), max_turns=2)
    # Se corta despues del primer turno de trabajo (max_turns=2 con plan + 1 read).
    provider = Scripted([text(PLAN), read("app.py"), read("app.py")])
    await harness(provider, planner=Planner(), checkpoints=store).run(task)
    snap = store.load(task.id)
    assert snap is not None and snap.plan

    provider2 = Scripted([text("app.py:1 define main().")])
    snap.task = snap.task.model_copy(update={"max_turns": 5})
    store.save(snap)
    out = await harness(provider2, planner=Planner(), checkpoints=store).resume(str(task.id))
    assert out.status is AgentStatus.COMPLETED
    assert len(provider2.calls) == 1, "replaneo al resumir"
    assert out.plan == snap.plan
