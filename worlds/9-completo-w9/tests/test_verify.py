"""Tests de la Fase 3: maquina de estados, verifier y repair loop.

El test que le da sentido a todo el modulo es
`test_rechaza_la_respuesta_sin_evidencia`: reproduce el fallo de la primera
corrida real -- el agente responde sin leer nada -- y comprueba que ahora el
harness lo rechaza en vez de aceptarlo.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from localforge.config import Settings
from localforge.harness import AgentHarness
from localforge.harness.state import IllegalTransition, StateMachine
from localforge.harness.verify import (
    NoHedgingVerifier,
    TrajectoryVerifier,
    Verdict,
    default_verifier,
)
from localforge.models import (
    AgentStatus,
    AgentTask,
    FailureReason,
    ModelResponse,
    StopReason,
    ToolCall,
)
from localforge.tools import default_registry

# ---------------------------------------------------------------------------
# Maquina de estados
# ---------------------------------------------------------------------------


def test_arranca_en_created() -> None:
    assert StateMachine().status is AgentStatus.CREATED


def test_camino_normal() -> None:
    m = StateMachine()
    m.to(AgentStatus.RUNNING)
    m.to(AgentStatus.WAITING_TOOL)
    m.to(AgentStatus.RUNNING)
    m.to(AgentStatus.VERIFYING)
    m.to(AgentStatus.COMPLETED)
    assert m.status is AgentStatus.COMPLETED


def test_camino_con_reparacion() -> None:
    m = StateMachine()
    m.to(AgentStatus.RUNNING)
    m.to(AgentStatus.VERIFYING)
    m.to(AgentStatus.REPAIRING)
    m.to(AgentStatus.RUNNING)
    m.to(AgentStatus.VERIFYING)
    m.to(AgentStatus.COMPLETED)
    assert m.repairs == 1


@pytest.mark.parametrize(
    "camino",
    [
        # No se puede completar sin pasar por verificacion.
        [AgentStatus.RUNNING, AgentStatus.COMPLETED],
        # No se puede saltar de created directo a trabajar con tools.
        [AgentStatus.WAITING_TOOL],
        # De esperar una tool no se va a verificar: primero se vuelve a running.
        [AgentStatus.RUNNING, AgentStatus.WAITING_TOOL, AgentStatus.VERIFYING],
        # Reparar no es un final.
        [AgentStatus.RUNNING, AgentStatus.VERIFYING, AgentStatus.REPAIRING, AgentStatus.COMPLETED],
    ],
)
def test_transiciones_prohibidas(camino: list[AgentStatus]) -> None:
    m = StateMachine()
    with pytest.raises(IllegalTransition):
        for step in camino:
            m.to(step)


def test_un_estado_terminal_no_se_mueve_mas() -> None:
    m = StateMachine()
    m.to(AgentStatus.RUNNING)
    m.to(AgentStatus.FAILED)
    with pytest.raises(IllegalTransition) as exc:
        m.to(AgentStatus.RUNNING)
    assert "terminal" in str(exc.value)


def test_se_puede_abortar_desde_cualquier_estado_no_terminal() -> None:
    for estado in (AgentStatus.CREATED, AgentStatus.RUNNING, AgentStatus.WAITING_TOOL):
        m = StateMachine()
        if estado is not AgentStatus.CREATED:
            m.to(AgentStatus.RUNNING)
        if estado is AgentStatus.WAITING_TOOL:
            m.to(AgentStatus.WAITING_TOOL)
        m.to(AgentStatus.FAILED)  # no debe levantar
        assert m.status is AgentStatus.FAILED


def test_el_error_dice_que_transiciones_si_se_pueden() -> None:
    m = StateMachine()
    m.to(AgentStatus.RUNNING)
    with pytest.raises(IllegalTransition) as exc:
        m.to(AgentStatus.COMPLETED)
    msg = str(exc.value)
    assert "verifying" in msg and "waiting_tool" in msg


def test_compact_path_colapsa_repeticiones() -> None:
    m = StateMachine()
    m.to(AgentStatus.RUNNING)
    for _ in range(3):
        m.to(AgentStatus.WAITING_TOOL)
        m.to(AgentStatus.RUNNING)
    assert "x3" in m.compact_path()


# ---------------------------------------------------------------------------
# Verifiers, aislados
# ---------------------------------------------------------------------------

TASK = AgentTask(objective="explicame el repo", repo_path="/tmp")


def test_trayectoria_acepta_si_leyo() -> None:
    assert TrajectoryVerifier().verify(TASK, "el repo hace X", ["read_file"]).ok


def test_trayectoria_acepta_si_busco() -> None:
    assert TrajectoryVerifier().verify(TASK, "el repo hace X", ["search_code"]).ok


def test_trayectoria_rechaza_si_no_leyo_nada() -> None:
    v = TrajectoryVerifier().verify(TASK, "el repo hace X", [])
    assert not v.ok
    assert "ninguna herramienta" in v.feedback


def test_listar_no_cuenta_como_evidencia() -> None:
    """El nucleo del fallo original: el agente listo y respondio."""
    v = TrajectoryVerifier().verify(TASK, "cli.py es el mas importante", ["list_files"])
    assert not v.ok
    assert "no abriste ninguno" in v.feedback
    # El feedback tiene que decir QUE hacer, no solo que estuvo mal.
    assert "search_code" in v.feedback and "read_file" in v.feedback


def test_hedging_se_rechaza_con_poca_evidencia() -> None:
    v = NoHedgingVerifier().verify(TASK, "posiblemente cli.py sea el entrypoint", ["read_file"])
    assert not v.ok
    assert "posiblemente" in v.feedback


def test_hedging_se_tolera_con_evidencia_abundante() -> None:
    """Hedgear sobre lo que no se leyo es correcto, no un fallo."""
    v = NoHedgingVerifier().verify(
        TASK,
        "probablemente el resto del repo siga el mismo patron",
        ["read_file", "read_file", "read_file", "search_code"],
    )
    assert v.ok


def test_composite_devuelve_el_primer_rechazo() -> None:
    v = default_verifier().verify(TASK, "posiblemente haga X", [])
    assert not v.ok
    # Primero el estructural: si no leyo nada, el reproche util es "lee algo".
    assert v.check == "trayectoria"


def test_composite_acepta_cuando_todos_aceptan() -> None:
    assert default_verifier().verify(TASK, "hace X, ver fs.py:31", ["read_file"]).ok


def test_verdict_rejected_exige_feedback() -> None:
    v = Verdict.rejected("x", "hace esto otro")
    assert not v.ok and v.feedback


# ---------------------------------------------------------------------------
# Integracion con el harness
# ---------------------------------------------------------------------------


class Scripted:
    name = model = "scripted"

    def __init__(self, responses: list[ModelResponse]) -> None:
        self.responses = list(responses)
        self.calls: list[list] = []

    async def complete(self, messages, tools=None, *, system=None, max_tokens=None):  # noqa: ANN001
        self.calls.append(list(messages))
        if not self.responses:
            return ModelResponse(content="sin mas", stop_reason=StopReason.END_TURN)
        return self.responses.pop(0)

    async def health(self):  # noqa: ANN201
        return {}

    async def aclose(self) -> None:
        return None


def text(content: str) -> ModelResponse:
    return ModelResponse(content=content, stop_reason=StopReason.END_TURN, input_tokens=10, output_tokens=5)


def call(name: str, **args) -> ModelResponse:
    return ModelResponse(
        tool_calls=[ToolCall(id=f"c_{name}_{len(args)}", name=name, arguments=args)],
        stop_reason=StopReason.TOOL_USE,
        input_tokens=10,
        output_tokens=5,
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / "app.py").write_text("def main():\n    return 1\n", encoding="utf-8")
    return tmp_path


def task_for(repo: Path, **kw) -> AgentTask:
    return AgentTask(objective="explicame el repo", repo_path=str(repo), max_turns=10, **kw)


async def test_rechaza_la_respuesta_sin_evidencia(repo: Path) -> None:
    """El fallo de la primera corrida real, ahora atrapado por el harness."""
    provider = Scripted([call("list_files"), text("posiblemente cli.py sea lo mas importante")])
    h = AgentHarness(provider, default_registry(), cfg=Settings(), max_repairs=0)
    out = await h.run(task_for(repo))

    assert out.status is AgentStatus.FAILED
    assert out.reason is FailureReason.VERIFICATION_FAILED
    assert out.rejected_by == ["trayectoria"]
    # La respuesta mala se devuelve igual: es peor no devolver nada.
    assert "cli.py" in out.output


async def test_el_repair_loop_le_da_otra_oportunidad(repo: Path) -> None:
    provider = Scripted(
        [
            call("list_files"),
            text("posiblemente sea un proyecto de Python"),   # rechazada
            call("read_file", path="app.py"),                 # se corrige
            text("app.py define main() que devuelve 1"),      # aceptada
        ]
    )
    h = AgentHarness(provider, default_registry(), cfg=Settings(), max_repairs=2)
    out = await h.run(task_for(repo))

    assert out.status is AgentStatus.COMPLETED
    assert out.repairs == 1
    assert "main()" in out.output
    assert "verifying -> repairing" in out.state_path


async def test_el_feedback_del_verifier_llega_al_modelo(repo: Path) -> None:
    provider = Scripted([call("list_files"), text("no lei nada"), call("read_file", path="app.py"), text("app.py define main")])
    h = AgentHarness(provider, default_registry(), cfg=Settings(), max_repairs=2)
    await h.run(task_for(repo))

    # En la tercera llamada el modelo tiene que haber recibido el reproche.
    ultimos = provider.calls[-1]
    textos = [m.content or "" for m in ultimos if m.role == "user"]
    assert any("read_file" in t for t in textos), textos


async def test_max_repairs_corta(repo: Path) -> None:
    # Siempre responde sin leer: el repair loop no puede salvarlo.
    provider = Scripted([text("no lei nada") for _ in range(10)])
    h = AgentHarness(provider, default_registry(), cfg=Settings(), max_repairs=2)
    out = await h.run(task_for(repo))

    assert out.reason is FailureReason.VERIFICATION_FAILED
    assert out.repairs == 2
    assert len(out.rejected_by) == 3  # el rechazo inicial + 2 reparaciones


async def test_el_camino_de_estados_queda_registrado(repo: Path) -> None:
    provider = Scripted([call("read_file", path="app.py"), text("app.py define main()")])
    h = AgentHarness(provider, default_registry(), cfg=Settings())
    out = await h.run(task_for(repo))

    assert out.status is AgentStatus.COMPLETED
    for estado in ("created", "running", "waiting_tool", "verifying", "completed"):
        assert estado in out.state_path
    assert out.repairs == 0


async def test_una_respuesta_fundamentada_pasa_derecho(repo: Path) -> None:
    provider = Scripted([call("read_file", path="app.py"), text("app.py:1 define main() que devuelve 1")])
    h = AgentHarness(provider, default_registry(), cfg=Settings())
    out = await h.run(task_for(repo))
    assert out.status is AgentStatus.COMPLETED
    assert out.repairs == 0
    assert out.rejected_by == []
