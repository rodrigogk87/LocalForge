from __future__ import annotations

from pathlib import Path

import pytest

from localforge.models import ToolCall
from localforge.tools import ToolExecutor, default_registry, safe_path
from localforge.tools.base import ToolError


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("def main():\n    return 42\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("# demo\n", encoding="utf-8")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "junk.js").write_text("x", encoding="utf-8")
    (tmp_path / "secret.png").write_bytes(b"\x89PNG")
    return tmp_path


def executor(repo: Path, **kw) -> ToolExecutor:
    return ToolExecutor(default_registry(), repo, timeout_s=5, **kw)


# --- contencion de rutas ----------------------------------------------------


@pytest.mark.parametrize(
    "attack",
    ["../outside.txt", "../../etc/passwd", "src/../../escape", "/etc/passwd"],
)
def test_safe_path_bloquea_traversal(repo: Path, attack: str) -> None:
    with pytest.raises(ToolError, match="fuera del workspace"):
        safe_path(repo, attack)


def test_safe_path_permite_dentro(repo: Path) -> None:
    assert safe_path(repo, "src/main.py") == (repo / "src" / "main.py").resolve()


# --- list_files -------------------------------------------------------------


async def test_list_files_ignora_ruido(repo: Path) -> None:
    result = await executor(repo).run_one(ToolCall(id="c1", name="list_files", arguments={}))
    assert result.success
    assert "main.py" in result.output
    assert "README.md" in result.output
    assert "node_modules" not in result.output  # directorio ignorado
    assert "secret.png" not in result.output  # binario ignorado


# --- read_file --------------------------------------------------------------


async def test_read_file_numera_lineas(repo: Path) -> None:
    result = await executor(repo).run_one(
        ToolCall(id="c2", name="read_file", arguments={"path": "src/main.py"})
    )
    assert result.success
    assert "return 42" in result.output
    assert "lineas 1-2 de 2" in result.output


async def test_read_file_inexistente_sugiere_vecinos(repo: Path) -> None:
    result = await executor(repo).run_one(
        ToolCall(id="c3", name="read_file", arguments={"path": "src/mian.py"})
    )
    assert not result.success
    # El error tiene que decir que SI existe cerca: sube la tasa de correccion.
    assert "main.py" in result.error


async def test_read_file_paginacion(repo: Path) -> None:
    big = repo / "big.txt"
    big.write_text("\n".join(f"linea {i}" for i in range(100)), encoding="utf-8")
    result = await executor(repo).run_one(
        ToolCall(id="c4", name="read_file", arguments={"path": "big.txt", "offset": 10, "limit": 5})
    )
    assert result.success
    assert "linea 10" in result.output and "linea 14" in result.output
    assert "linea 15" not in result.output
    assert "offset=15" in result.output  # le dice como seguir


# --- contrato del executor --------------------------------------------------


async def test_tool_desconocida_lista_las_validas(repo: Path) -> None:
    result = await executor(repo).run_one(ToolCall(id="c5", name="read_files", arguments={}))
    assert not result.success
    assert "read_file" in result.error and "list_files" in result.error


async def test_argumentos_invalidos_dicen_el_campo(repo: Path) -> None:
    result = await executor(repo).run_one(
        ToolCall(id="c6", name="read_file", arguments={"path": ""})
    )
    assert not result.success
    assert "path" in result.error


async def test_falta_argumento_requerido(repo: Path) -> None:
    result = await executor(repo).run_one(ToolCall(id="c7", name="read_file", arguments={}))
    assert not result.success
    assert "path" in result.error


async def test_truncado_avisa_que_trunco(repo: Path) -> None:
    (repo / "huge.txt").write_text("x" * 50_000, encoding="utf-8")
    result = await executor(repo, output_limit=500).run_one(
        ToolCall(id="c8", name="read_file", arguments={"path": "huge.txt"})
    )
    assert result.success and result.truncated
    assert "truncado" in result.output


async def test_run_all_preserva_correlacion(repo: Path) -> None:
    calls = [
        ToolCall(id="a", name="read_file", arguments={"path": "README.md"}),
        ToolCall(id="b", name="list_files", arguments={}),
        ToolCall(id="c", name="read_file", arguments={"path": "src/main.py"}),
    ]
    results = await executor(repo).run_all(calls)
    by_id = {r.call_id: r for r in results}
    assert set(by_id) == {"a", "b", "c"}
    assert "# demo" in by_id["a"].output
    assert "return 42" in by_id["c"].output
