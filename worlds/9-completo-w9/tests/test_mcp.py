"""Cliente MCP (W4, lo que el mundo dejo pendiente).

Contra un server REAL: `examples/mcp_server_demo.py` corre como proceso aparte y
se le habla por stdio con el handshake completo. Es lo que PROJECT_STATE pedia:
"testearlo de verdad necesita un server MCP real contra el que hablar".
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from localforge.config import Settings
from localforge.harness import AgentHarness
from localforge.mcp import MCPClient, MCPError, MCPSession, ServerConfig, load_config
from localforge.models import AgentStatus, AgentTask, ModelResponse, StopReason, ToolCall
from localforge.permissions import AutoApprover, Decision, default_policy
from localforge.tools import default_registry

DEMO = Path(__file__).resolve().parent.parent / "examples" / "mcp_server_demo.py"


def demo(name: str = "demo") -> ServerConfig:
    return ServerConfig(name=name, command=sys.executable, args=(str(DEMO),))


async def test_handshake_y_descubrimiento() -> None:
    client = MCPClient(demo())
    await client.start()
    try:
        assert client.server_info["name"] == "demo"
        names = [t["name"] for t in await client.list_tools()]
        assert names == ["add", "word_count", "fail"]
    finally:
        await client.close()
    assert client.proc is None


async def test_las_tools_entran_con_prefijo_y_su_schema() -> None:
    async with MCPSession([demo()]) as session:
        reg = default_registry()
        nombres = session.register_into(reg)
        assert nombres == ["mcp__demo__add", "mcp__demo__word_count", "mcp__demo__fail"]
        defs = {d.name: d for d in reg.definitions()}
        # El modelo ve el schema DEL SERVER, no uno inventado por nosotros.
        assert defs["mcp__demo__add"].input_schema["required"] == ["a", "b"]
        assert defs["mcp__demo__add"].description.startswith("[MCP · demo]")


async def test_dos_servers_con_la_misma_tool_no_se_pisan() -> None:
    async with MCPSession([demo("uno"), demo("dos")]) as session:
        reg = default_registry()
        session.register_into(reg)
        assert {"mcp__uno__add", "mcp__dos__add"} <= set(reg.names())


async def test_un_server_roto_no_impide_arrancar() -> None:
    roto = ServerConfig(name="roto", command="/no/existe")
    async with MCPSession([roto, demo()]) as session:
        assert session.errors and "roto" in session.errors[0]
        assert any(t.name == "mcp__demo__add" for t in session.tools)


async def test_una_tool_mcp_cae_en_ask_por_construccion() -> None:
    """Nadie escribio una regla para MCP: la politica no la conoce y pregunta."""
    v = default_policy().decide(ToolCall(id="1", name="mcp__demo__add", arguments={"a": 1, "b": 2}))
    assert v.decision is Decision.ASK


async def test_el_agente_usa_la_tool_remota_y_un_error_remoto_es_feedback(tmp_path: Path) -> None:
    class Scripted:
        name = model = "s"

        def __init__(self) -> None:
            self.r = [
                ModelResponse(
                    tool_calls=[
                        ToolCall(id="a", name="mcp__demo__add", arguments={"a": 2, "b": 40}),
                        ToolCall(id="f", name="mcp__demo__fail", arguments={}),
                        ToolCall(id="r", name="read_file", arguments={"path": "x.py"}),
                    ],
                    stop_reason=StopReason.TOOL_USE,
                ),
                ModelResponse(content="42, segun x.py:1", stop_reason=StopReason.END_TURN),
            ]

        async def complete(self, *a, **k):  # noqa: ANN002, ANN003, ANN202
            return self.r.pop(0)

        async def health(self):  # noqa: ANN201
            return {}

        async def aclose(self) -> None:
            return None

    (tmp_path / "x.py").write_text("X = 42\n", encoding="utf-8")
    results = []
    async with MCPSession([demo()]) as session:
        reg = default_registry()
        session.register_into(reg)
        h = AgentHarness(
            Scripted(), reg, cfg=Settings(), approver=AutoApprover(),
            on_event=lambda e, **p: results.extend(p["results"]) if e == "tools_done" else None,
        )
        out = await h.run(AgentTask(objective="suma", repo_path=str(tmp_path)))
    assert out.status is AgentStatus.COMPLETED
    by = {r.name: r for r in results}
    assert by["mcp__demo__add"].success and by["mcp__demo__add"].output == "42"
    assert not by["mcp__demo__fail"].success and "fallo a proposito" in by["mcp__demo__fail"].error


async def test_sin_aprobacion_no_se_llama_al_server(tmp_path: Path) -> None:
    async with MCPSession([demo()]) as session:
        tool = next(t for t in session.tools if t.remote_name == "add")
        from localforge.tools.base import ToolExecutor
        from localforge.tools import ToolRegistry

        ex = ToolExecutor(ToolRegistry([tool]), tmp_path)  # approver default: deniega
        res = await ex.run_one(ToolCall(id="1", name=tool.name, arguments={"a": 1, "b": 1}))
        assert not res.success and "permiso denegado" in res.error


def test_la_config_tiene_el_formato_de_claude(tmp_path: Path) -> None:
    p = tmp_path / "mcp.json"
    p.write_text(json.dumps({"mcpServers": {"x": {"command": "python", "args": ["s.py"], "env": {"A": "1"}}}}))
    (cfg,) = load_config(p)
    assert cfg.name == "x" and cfg.args == ("s.py",) and cfg.env == {"A": "1"}
    p.write_text(json.dumps({"mcpServers": {"x": {"args": []}}}))
    with pytest.raises(MCPError, match="command"):
        load_config(p)


def test_la_config_no_se_busca_en_el_repo_analizado() -> None:
    """Un mcp.json en el repo seria ejecucion de codigo gratis para un repo
    hostil. Nada en el paquete lo busca: la config entra solo por --mcp."""
    src = Path(__file__).resolve().parent.parent / "src" / "localforge"
    for f in src.rglob("*.py"):
        texto = f.read_text(encoding="utf-8")
        if f.name in ("mcp.py", "cli.py"):
            continue
        assert "mcp.json" not in texto, f"{f.name} busca un mcp.json"
