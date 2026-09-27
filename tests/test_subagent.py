"""Tests de subagentes (Fase 8 + el aislamiento que la Fase 2 dejo pendiente).

La propiedad central: **lo que el subagente leyo NO entra al contexto del padre.**
Si eso se rompe, delegar cuesta mas que no delegar y la feature es peor que nada.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from localforge.config import Settings
from localforge.harness import AgentHarness
from localforge.agents import (
    MAX_DEPTH,
    SubagentArgs,
    SubagentTool,
    registry_with_subagents,
)
from localforge.models import AgentStatus, AgentTask, ModelResponse, StopReason, ToolCall
from localforge.sandbox import AutoApprover
from localforge.tools import ToolError


class Scripted:
    """Provider con un guion por objetivo, para distinguir padre de hijo."""

    name = model = "scripted"

    def __init__(self, guiones: dict[str, list[ModelResponse]]) -> None:
        self.guiones = {k: list(v) for k, v in guiones.items()}
        self.prompts: list[list] = []

    async def complete(self, messages, tools=None, *, system=None, max_tokens=None):  # noqa: ANN001
        self.prompts.append(list(messages))
        objetivo = next((m.content or "" for m in messages if m.role == "user"), "")
        for clave, respuestas in self.guiones.items():
            if clave in objetivo and respuestas:
                return respuestas.pop(0)
        return ModelResponse(content="listo", stop_reason=StopReason.END_TURN, input_tokens=50)

    async def health(self):  # noqa: ANN201
        return {}

    async def aclose(self) -> None:
        return None


def text(c: str, tokens: int = 50) -> ModelResponse:
    return ModelResponse(content=c, stop_reason=StopReason.END_TURN, input_tokens=tokens, output_tokens=10)


def call(name: str, **args) -> ModelResponse:
    return ModelResponse(
        tool_calls=[ToolCall(id=f"c_{name}_{abs(hash(str(args))) % 999}", name=name, arguments=args)],
        stop_reason=StopReason.TOOL_USE,
        input_tokens=50,
        output_tokens=10,
    )


SECRETO_DEL_HIJO = "XDETALLEQUEELPADRENODEBEVERX"


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    for i in range(3):
        (tmp_path / f"f{i}.py").write_text(f"# {SECRETO_DEL_HIJO}\ndef f{i}(): pass\n", encoding="utf-8")
    return tmp_path


# --- la propiedad central ---------------------------------------------------


async def test_lo_que_leyo_el_hijo_no_entra_al_contexto_del_padre(repo: Path) -> None:
    provider = Scripted(
        {
            "investiga": [  # el hijo: lee los tres archivos
                call("read_file", path="f0.py"),
                call("read_file", path="f1.py"),
                call("read_file", path="f2.py"),
                text("los tres archivos definen funciones vacias"),
            ],
            "tarea del padre": [
                call("delegate", objective="investiga los archivos"),
                text("el repo define tres funciones vacias, segun el subagente"),
            ],
        }
    )
    registry = registry_with_subagents(provider, cfg=Settings(), approver=AutoApprover())
    harness = AgentHarness(
        provider, registry, cfg=Settings(), approver=AutoApprover(),
        verifier=_Permisivo(),
    )
    out = await harness.run(AgentTask(objective="tarea del padre", repo_path=str(repo), max_turns=6))

    assert out.status is AgentStatus.COMPLETED

    # El padre solo llamo a `delegate`, no a read_file.
    assert out.trajectory == ["delegate"]

    # Y lo critico: el contenido de los archivos que leyo el hijo NO aparece en
    # ningun mensaje que se le haya mandado al modelo en nombre del padre.
    prompts_del_padre = [
        p for p in provider.prompts
        if any("tarea del padre" in (m.content or "") for m in p)
    ]
    assert prompts_del_padre, "no hubo prompts del padre"
    for prompt in prompts_del_padre:
        for m in prompt:
            assert SECRETO_DEL_HIJO not in (m.content or ""), (
                "el contenido leido por el subagente se filtro al contexto del padre"
            )


async def test_el_padre_recibe_la_conclusion_y_el_costo(repo: Path) -> None:
    provider = Scripted(
        {
            "investiga": [call("read_file", path="f0.py"), text("f0 define f0()")],
            "padre": [call("delegate", objective="investiga f0"), text("ok")],
        }
    )
    tool = SubagentTool(provider, registry_factory=lambda d: registry_with_subagents(provider, cfg=Settings()), cfg=Settings())
    out = await tool.run(repo, SubagentArgs(objective="investiga f0"))

    assert "f0 define f0()" in out
    # El encabezado reporta el costo: delegar no puede ser invisible.
    assert "[subagente:" in out and "tokens" in out


# --- limite de profundidad --------------------------------------------------


def test_el_hijo_no_tiene_la_tool_de_delegar() -> None:
    """El limite no se le pide al modelo: se le quita la posibilidad."""
    provider = Scripted({})
    raiz = registry_with_subagents(provider, cfg=Settings())
    assert "delegate" in raiz.names()

    # La fabrica en profundidad MAX_DEPTH no registra la tool.
    tool = raiz.get("delegate")
    hijo = tool.registry_factory(MAX_DEPTH)
    assert "delegate" not in hijo.names()


async def test_delegar_en_el_limite_es_un_error_claro(repo: Path) -> None:
    provider = Scripted({})
    tool = SubagentTool(
        provider,
        registry_factory=lambda d: registry_with_subagents(provider, cfg=Settings()),
        cfg=Settings(),
        depth=MAX_DEPTH,
    )
    with pytest.raises(ToolError, match="anidamiento"):
        await tool.run(repo, SubagentArgs(objective="x"))


# --- fallos del hijo --------------------------------------------------------


async def test_un_hijo_que_no_termina_es_un_resultado_no_una_excepcion(repo: Path) -> None:
    # El hijo pide tools para siempre: se queda sin turnos.
    provider = Scripted({"investiga": [call("read_file", path="f0.py") for _ in range(20)]})
    tool = SubagentTool(
        provider,
        registry_factory=lambda d: registry_with_subagents(provider, cfg=Settings()),
        cfg=Settings(),
    )
    out = await tool.run(repo, SubagentArgs(objective="investiga sin parar", max_turns=2))

    assert "NO pudo terminar" in out
    assert "max_turns" in out
    # Le dice al padre que hacer al respecto.
    assert "mas acotada" in out


# --- integracion ------------------------------------------------------------


def test_el_registry_raiz_tiene_lectura_y_delegacion() -> None:
    registry = registry_with_subagents(Scripted({}), cfg=Settings())
    assert set(registry.names()) >= {"list_files", "read_file", "search_code", "delegate"}


def test_delegate_necesita_aprobacion_por_defecto() -> None:
    """No esta en la lista de lectura, asi que cae en ASK como cualquier tool nueva."""
    from localforge.models import ToolCall as TC
    from localforge.sandbox import Decision, default_policy

    verdict = default_policy().decide(TC(id="c", name="delegate", arguments={"objective": "x"}))
    assert verdict.decision is Decision.ASK


class _Permisivo:
    name = "test"

    def verify(self, task, answer, trajectory):  # noqa: ANN001, ANN201
        from localforge.harness.verify import Verdict

        return Verdict.passed(self.name)
