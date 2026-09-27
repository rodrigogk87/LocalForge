"""Tests de search_code.

El test que le da sentido a toda la tool es `test_encuentra_lo_que_el_agente_no
_supo_encontrar`: reproduce el fallo medido en el M1, donde el agente buscaba
`safe_path`, abrio el archivo equivocado y concluyo con "el mas probable lugar".
"""

from __future__ import annotations

from pathlib import Path

import pytest

from localforge.tools import ToolError, default_registry
from localforge.tools.search import MAX_LINE_CHARS, SearchCodeArgs, SearchCodeTool

TOOL = SearchCodeTool()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """Un repo chico que imita la forma del real."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "cli.py").write_text(
        "import argparse\n\n\ndef main():\n    parser = argparse.ArgumentParser()\n",
        encoding="utf-8",
    )
    (tmp_path / "src" / "fs.py").write_text(
        "from pathlib import Path\n"
        "\n"
        "\n"
        "def safe_path(workspace: Path, requested: str) -> Path:\n"
        '    """Resuelve una ruta y verifica que caiga dentro del workspace."""\n'
        "    root = workspace.resolve()\n"
        "    candidate = (root / requested).resolve()\n"
        "    if candidate != root and root not in candidate.parents:\n"
        '        raise ToolError("ruta fuera del workspace")\n'
        "    return candidate\n",
        encoding="utf-8",
    )
    (tmp_path / "README.md").write_text("# demo\n\nUsa safe_path para contener rutas.\n", encoding="utf-8")
    # Ruido que la busqueda tiene que ignorar.
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("safe_path en un archivo de git\n", encoding="utf-8")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "x.js").write_text("safe_path en node_modules\n", encoding="utf-8")
    (tmp_path / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n safe_path")
    return tmp_path


async def search(repo: Path, **kw) -> str:
    return await TOOL.run(repo, SearchCodeArgs(**kw))


# --- el caso que motivo la tool --------------------------------------------


async def test_encuentra_lo_que_el_agente_no_supo_encontrar(repo: Path) -> None:
    out = await search(repo, pattern="safe_path")
    # Lo importante: dice el ARCHIVO y la LINEA, que es lo que el agente no pudo
    # deducir del listado.
    assert "src/fs.py" in out
    assert "def safe_path" in out
    assert ":" in out  # numero de linea
    # Y no manda al archivo equivocado.
    assert "src/cli.py" not in out


# --- correccion basica ------------------------------------------------------


async def test_reporta_ruta_y_numero_de_linea(repo: Path) -> None:
    out = await search(repo, pattern="ArgumentParser")
    assert "src/cli.py" in out
    assert "    5:" in out  # la linea exacta


async def test_case_insensitive_por_defecto(repo: Path) -> None:
    assert "src/fs.py" in await search(repo, pattern="SAFE_PATH")


async def test_case_sensitive_cuando_se_pide(repo: Path) -> None:
    out = await search(repo, pattern="SAFE_PATH", case_sensitive=True)
    assert "sin coincidencias" in out


async def test_busqueda_literal_no_interpreta_regex(repo: Path) -> None:
    # Sin regex=true, los parentesis son texto, no agrupacion.
    out = await search(repo, pattern="workspace.resolve()")
    assert "src/fs.py" in out


async def test_regex_cuando_se_pide(repo: Path) -> None:
    out = await search(repo, pattern=r"def \w+\(", regex=True)
    assert "src/fs.py" in out
    assert "src/cli.py" in out


async def test_glob_filtra_por_tipo(repo: Path) -> None:
    out = await search(repo, pattern="safe_path", glob="*.md")
    assert "README.md" in out
    assert "src/fs.py" not in out


async def test_path_acota_la_busqueda(repo: Path) -> None:
    out = await search(repo, pattern="safe_path", path="src")
    assert "src/fs.py" in out
    assert "README.md" not in out


async def test_context_lines(repo: Path) -> None:
    out = await search(repo, pattern="raise ToolError", context_lines=2)
    assert "if candidate != root" in out  # una linea anterior
    assert "return candidate" in out  # una posterior


# --- ruido ------------------------------------------------------------------


async def test_ignora_git_node_modules_y_binarios(repo: Path) -> None:
    out = await search(repo, pattern="safe_path")
    assert ".git" not in out
    assert "node_modules" not in out
    assert "logo.png" not in out


# --- errores accionables ----------------------------------------------------


async def test_sin_resultados_explica_que_se_busco(repo: Path) -> None:
    out = await search(repo, pattern="funcion_que_no_existe")
    # "No hay" tiene que distinguirse de "busque mal": dice donde busco,
    # cuantos archivos revisó y que probar.
    assert "sin coincidencias" in out
    assert "archivos" in out
    assert "probá" in out


async def test_regex_invalida_dice_como_salir_del_paso(repo: Path) -> None:
    with pytest.raises(ToolError) as exc:
        await search(repo, pattern="def (", regex=True)
    assert "invalida" in str(exc.value)
    assert "sin regex=true" in str(exc.value)


async def test_path_inexistente_es_error_claro(repo: Path) -> None:
    with pytest.raises(ToolError) as exc:
        await search(repo, pattern="x", path="no_existe")
    assert "no existe" in str(exc.value)


async def test_no_se_escapa_del_workspace(repo: Path) -> None:
    with pytest.raises(ToolError) as exc:
        await search(repo, pattern="root", path="../..")
    assert "fuera del workspace" in str(exc.value)


# --- limites ----------------------------------------------------------------


async def test_trunca_y_avisa_como_acotar(repo: Path) -> None:
    (repo / "muchos.txt").write_text("aguja\n" * 200, encoding="utf-8")
    out = await search(repo, pattern="aguja", max_results=10)
    assert "cortado en 10" in out
    assert "path=" in out


async def test_recorta_lineas_larguisimas(repo: Path) -> None:
    (repo / "min.js").write_text("x" * 5_000 + "aguja\n", encoding="utf-8")
    out = await search(repo, pattern="aguja")
    assert "chars]" in out
    # Ninguna linea del resultado puede ser gigante.
    assert max(len(l) for l in out.splitlines()) < MAX_LINE_CHARS + 120


async def test_resultados_estables_entre_corridas(repo: Path) -> None:
    (repo / "a.txt").write_text("aguja\n", encoding="utf-8")
    (repo / "b.txt").write_text("aguja\n", encoding="utf-8")
    primera = await search(repo, pattern="aguja", max_results=2)
    for _ in range(4):
        assert await search(repo, pattern="aguja", max_results=2) == primera


# --- integracion con el registry -------------------------------------------


def test_esta_en_el_registry_por_defecto() -> None:
    assert "search_code" in default_registry().names()


def test_el_schema_que_ve_el_modelo_documenta_los_args() -> None:
    definition = next(d for d in default_registry().definitions() if d.name == "search_code")
    props = definition.input_schema["properties"]
    assert set(props) >= {"pattern", "path", "regex", "glob", "context_lines", "max_results"}
    # `pattern` es el unico obligatorio: el resto tiene default.
    assert definition.input_schema["required"] == ["pattern"]
    # La descripcion tiene que decirle CUANDO usarla, no solo que hace.
    assert "ANTES de read_file" in definition.description
