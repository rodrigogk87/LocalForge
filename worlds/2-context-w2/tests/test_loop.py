"""Tests del agent loop.

Aca SI hay un provider scripteado, y eso no contradice la regla de "nada de
FakeModelProvider": la regla es sobre el PRODUCTO, que habla con un LLM real
desde el minuto cero. Para testear el harness necesitamos determinismo -- que
el modelo devuelva exactamente una tool call invalida, o que se repita tres
veces -- y eso es imposible contra un modelo real no determinista y lento.

Sin este provider, la logica mas critica del sistema (manejo de fallos y
terminacion) seria la unica parte sin tests.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from localforge.config import Settings
from localforge.harness import AgentHarness
from localforge.models import (
    AgentMessage,
    AgentStatus,
    AgentTask,
    FailureReason,
    ModelResponse,
    StopReason,
    ToolCall,
    ToolDefinition,
)
from localforge.tools import default_registry


class ScriptedProvider:
    """Devuelve respuestas prearmadas, en orden. Cumple ModelProvider."""

    name = "scripted"
    model = "scripted"

    def __init__(self, responses: list[ModelResponse]) -> None:
        self.responses = list(responses)
        self.calls: list[list[AgentMessage]] = []
        self.systems: list[str | None] = []

    async def complete(
        self,
        messages: list[AgentMessage],
        tools: list[ToolDefinition] | None = None,
        *,
        system: str | None = None,
        max_tokens: int | None = None,
    ) -> ModelResponse:
        self.calls.append(list(messages))
        self.systems.append(system)
        if not self.responses:
            return ModelResponse(content="sin mas respuestas", stop_reason=StopReason.END_TURN)
        return self.responses.pop(0)

    async def health(self) -> dict[str, str]:
        return {"provider": self.name, "model": self.model}

    async def aclose(self) -> None:
        return None


def text(content: str) -> ModelResponse:
    return ModelResponse(
        content=content, stop_reason=StopReason.END_TURN, input_tokens=10, output_tokens=5
    )


def tool(name: str, **arguments: object) -> ModelResponse:
    return ModelResponse(
        tool_calls=[ToolCall(id=f"c_{name}_{len(arguments)}", name=name, arguments=arguments)],
        stop_reason=StopReason.TOOL_USE,
        input_tokens=10,
        output_tokens=5,
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / "app.py").write_text("print('hola')\n", encoding="utf-8")
    return tmp_path


def task_for(repo: Path, **kw) -> AgentTask:
    defaults = dict(objective="explicame el repo", repo_path=str(repo), max_turns=10)
    return AgentTask(**{**defaults, **kw})


def harness(provider: ScriptedProvider) -> AgentHarness:
    return AgentHarness(provider, default_registry(), cfg=Settings())


# --- camino feliz -----------------------------------------------------------


async def test_ciclo_completo_tool_y_respuesta(repo: Path) -> None:
    provider = ScriptedProvider([tool("list_files"), text("Es un proyecto de un archivo.")])
    outcome = await harness(provider).run(task_for(repo))

    assert outcome.status is AgentStatus.COMPLETED
    assert outcome.turns == 2
    assert outcome.trajectory == ["list_files"]
    assert "un archivo" in outcome.output
    assert outcome.total_tokens == 30

    # El resultado de la tool tiene que haber vuelto al contexto del modelo.
    segundo_turno = provider.calls[1]
    roles = [m.role for m in segundo_turno]
    assert roles == ["user", "assistant", "tool"]
    assert "app.py" in (segundo_turno[-1].content or "")
    assert segundo_turno[-1].tool_call_id == segundo_turno[-2].tool_calls[0].id


async def test_el_system_prompt_llega_al_provider(repo: Path) -> None:
    provider = ScriptedProvider([text("listo")])
    await harness(provider).run(task_for(repo))
    system = provider.systems[0]
    assert system and "list_files" in system and repo.name in system


# --- errores como feedback --------------------------------------------------


async def test_error_de_tool_no_mata_al_agente(repo: Path) -> None:
    provider = ScriptedProvider(
        [tool("read_file", path="no_existe.py"), text("El archivo no estaba.")]
    )
    outcome = await harness(provider).run(task_for(repo))

    assert outcome.status is AgentStatus.COMPLETED
    contenido_tool = provider.calls[1][-1].content or ""
    assert contenido_tool.startswith("ERROR:")
    assert "no existe" in contenido_tool


async def test_tool_inexistente_vuelve_como_feedback(repo: Path) -> None:
    provider = ScriptedProvider([tool("leer_archivo", path="app.py"), text("corregido")])
    outcome = await harness(provider).run(task_for(repo))
    assert outcome.status is AgentStatus.COMPLETED
    assert "tool desconocida" in (provider.calls[1][-1].content or "")


# --- terminacion ------------------------------------------------------------


async def test_max_turns(repo: Path) -> None:
    provider = ScriptedProvider([tool("list_files", path=str(i)) for i in range(20)])
    outcome = await harness(provider).run(task_for(repo, max_turns=3))
    assert outcome.status is AgentStatus.FAILED
    assert outcome.reason is FailureReason.MAX_TURNS
    assert outcome.turns == 3


async def test_deteccion_de_loop(repo: Path) -> None:
    # Misma tool, mismos argumentos, tres veces: el modelo no progresa.
    provider = ScriptedProvider([tool("list_files") for _ in range(6)])
    outcome = await harness(provider).run(task_for(repo, max_turns=10))
    assert outcome.status is AgentStatus.FAILED
    assert outcome.reason is FailureReason.LOOP_DETECTED
    assert outcome.turns == 3


async def test_token_budget(repo: Path) -> None:
    provider = ScriptedProvider([tool("list_files", path=str(i)) for i in range(20)])
    outcome = await harness(provider).run(task_for(repo, token_budget=20, max_turns=10))
    assert outcome.status is AgentStatus.FAILED
    assert outcome.reason is FailureReason.TOKEN_BUDGET


async def test_respuesta_truncada_no_es_resultado_final(repo: Path) -> None:
    truncada = ModelResponse(content="a medio de", stop_reason=StopReason.MAX_TOKENS)
    provider = ScriptedProvider([truncada, text("ahora si, completa")])
    outcome = await harness(provider).run(task_for(repo))

    assert outcome.status is AgentStatus.COMPLETED
    assert outcome.output == "ahora si, completa"
    # Se le pidio continuar en vez de aceptar el texto cortado.
    assert "cortada" in (provider.calls[1][-1].content or "")


async def test_repo_inexistente_falla_limpio(tmp_path: Path) -> None:
    provider = ScriptedProvider([text("no deberia llamarse")])
    outcome = await harness(provider).run(
        task_for(tmp_path, repo_path=str(tmp_path / "no_existe"))
    )
    assert outcome.status is AgentStatus.FAILED
    assert provider.calls == []  # ni siquiera se llamo al modelo


# --- trayectoria ------------------------------------------------------------


async def test_trayectoria_y_registros(repo: Path) -> None:
    provider = ScriptedProvider(
        [tool("list_files"), tool("read_file", path="app.py"), text("listo")]
    )
    outcome = await harness(provider).run(task_for(repo))
    assert outcome.trajectory == ["list_files", "read_file"]
    assert [r.turn for r in outcome.turn_records] == [1, 2, 3]
    assert outcome.turn_records[1].tool_results[0].success
