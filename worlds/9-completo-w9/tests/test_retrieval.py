"""Retrieval just-in-time (W2-C11), lo que la Fase 2 dejo pendiente.

Tres propiedades: trae el fragmento correcto aunque la pregunta no nombre el
identificador exacto, NO indexa secretos (porque `retrieve` no tiene un `path`
que la politica pueda mirar), y lo que trae se mide como su propia capa.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from localforge.harness.context import ContextBudget, ContextBuilder
from localforge.harness.verify import EVIDENCE_TOOLS, TrajectoryVerifier
from localforge.models import AgentMessage, AgentTask, ToolCall
from localforge.permissions import Decision, default_policy
from localforge.retrieval import Index, chunk_file, tokenize
from localforge.tools.retrieve import RetrieveArgs, RetrieveTool
from localforge.tools import ToolError, default_registry

FS = '''"""Tools de filesystem."""

import os


def safe_path(workspace, requested):
    """Verifica que la ruta no escape del workspace."""
    root = workspace.resolve()
    candidate = (root / requested).resolve()
    if root not in candidate.parents:
        raise ValueError("ruta fuera del workspace")
    return candidate


def human_size(n):
    return f"{n}B"
'''

CLI = '''import argparse


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("repo")
    return parser.parse_args()
'''


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "fs.py").write_text(FS, encoding="utf-8")
    (tmp_path / "cli.py").write_text(CLI, encoding="utf-8")
    (tmp_path / "README.md").write_text("# Demo\n\nUn proyecto de prueba.\n", encoding="utf-8")
    (tmp_path / ".env").write_text("DB_PASSWORD=hunter2\nworkspace_secret=1\n", encoding="utf-8")
    return tmp_path


# --- tokenizacion y troceo ----------------------------------------------------


def test_los_identificadores_se_parten_y_se_conservan_enteros() -> None:
    toks = tokenize("safe_path ToolExecutor")
    assert {"safe_path", "safe", "path", "toolexecutor", "tool", "executor"} <= set(toks)


def test_python_se_trocea_por_funcion_y_no_corta_una_a_la_mitad() -> None:
    chunks = chunk_file("tools/fs.py", FS)
    cuerpos = [c.text for c in chunks]
    # El encabezado (docstring + imports) es su propio fragmento.
    assert cuerpos[0].startswith('"""Tools')
    safe = next(c for c in chunks if "def safe_path" in c.text)
    assert "return candidate" in safe.text, "el def quedo partido"
    assert "def human_size" not in safe.text
    assert safe.start == 6


def test_lo_que_no_es_python_va_por_ventanas_con_solapamiento() -> None:
    texto = "\n".join(f"linea {i}" for i in range(100))
    chunks = chunk_file("notas.txt", texto)
    assert len(chunks) > 2
    assert chunks[1].start <= chunks[0].end, "sin solapamiento"


# --- ranking ------------------------------------------------------------------


def test_una_pregunta_en_lenguaje_natural_encuentra_la_funcion(repo: Path) -> None:
    idx = Index.build(repo)
    hits = idx.search("donde se valida que la ruta no escape del workspace", k=2)
    assert hits[0][1].path == "tools/fs.py"
    assert "def safe_path" in hits[0][1].text


def test_el_nombre_del_archivo_cuenta_como_contenido(repo: Path) -> None:
    hits = Index.build(repo).search("cli", k=1)
    assert hits[0][1].path == "cli.py"


# --- seguridad ----------------------------------------------------------------


def test_los_archivos_sensibles_no_se_indexan(repo: Path) -> None:
    """`read_file('.env')` lo frena la politica, que mira el path. `retrieve` no
    tiene path: si el .env estuviera en el indice, la politica no se enteraria."""
    idx = Index.build(repo)
    assert ".env" in idx.skipped_sensitive
    assert all(c.path != ".env" for c in idx.chunks)
    assert not any("hunter2" in c.text for c in idx.chunks)


async def test_la_tool_no_devuelve_secretos_ni_preguntando_por_ellos(repo: Path) -> None:
    tool = RetrieveTool()
    try:
        out = await tool.run(repo, RetrieveArgs(query="DB_PASSWORD password secret"))
    except ToolError:
        return  # no encontrar nada tambien es correcto
    assert "hunter2" not in out


# --- integracion con el resto del harness ---------------------------------------


async def test_la_salida_trae_fragmentos_numerados_con_su_ubicacion(repo: Path) -> None:
    out = await RetrieveTool().run(repo, RetrieveArgs(query="safe_path workspace", k=1))
    assert "--- tools/fs.py:6-12" in out
    assert "    6  def safe_path" in out


async def test_sin_coincidencias_es_un_error_que_dice_que_hacer(repo: Path) -> None:
    with pytest.raises(ToolError, match="otras palabras"):
        await RetrieveTool().run(repo, RetrieveArgs(query="zzqx kkwy"))


def test_esta_en_el_registry_y_la_politica_lo_permite() -> None:
    assert "retrieve" in default_registry().names()
    verdict = default_policy().decide(ToolCall(id="1", name="retrieve", arguments={"query": "x"}))
    assert verdict.decision is Decision.ALLOW


def test_cuenta_como_evidencia_para_el_verifier() -> None:
    """Devuelve codigo real con numeros de linea: es tan evidencia como read_file."""
    assert "retrieve" in EVIDENCE_TOOLS
    task = AgentTask(objective="x", repo_path=".")
    assert TrajectoryVerifier().verify(task, "r", ["list_files", "retrieve"]).ok


def test_lo_que_trae_se_mide_como_capa_retrieved() -> None:
    msgs = [
        AgentMessage(role="user", content="tarea"),
        AgentMessage(role="tool", content="x" * 900, tool_name="retrieve", tool_call_id="a"),
        AgentMessage(role="tool", content="y" * 300, tool_name="read_file", tool_call_id="b"),
    ]
    bd = ContextBuilder(ContextBudget(limit=32_768)).build(
        system="sys", task="tarea", messages=msgs, definitions=[]
    ).breakdown
    assert bd.layer("retrieved").present and bd.layer("retrieved").tokens > 0
    # Cada mensaje en UNA capa: el total no duplica.
    assert bd.layer("observations").chars == 300
    assert bd.total == sum(l.tokens for l in bd.layers)
