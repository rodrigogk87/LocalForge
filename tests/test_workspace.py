"""Los subproyectos declaran lo que importan, y nada mas.

Por que esto existe. El proyecto ya tuvo un import circular real:
`permissions.py` vivia en `harness/`, `tools/base.py` lo importaba, y
`harness/` importaba `tools/`. **Los 85 tests de entonces pasaban igual**, porque
la suite importaba en un orden que funcionaba. Explotaba solo si importabas
`localforge.tools` primero.

En un env compartido, un import no declarado sigue funcionando: todo esta
instalado junto. Lo que lo convierte en un error es el limite del paquete, y
estos tests son los que lo hacen cumplir sin necesidad de once envs:

  - si un subproyecto importa algo que no declara, falla
  - si declara algo que no importa, falla (dependencias muertas)
  - si el grafo tiene un ciclo, falla

El chequeo de verdad, con envs aislados, es
`uv run --package localforge-tools python -c "import localforge.tools"`, que
falla con ModuleNotFoundError. Eso no se corre en la suite porque construye un
env por paquete y tarda; estos tests dan la misma garantia leyendo el AST.
"""

from __future__ import annotations

import ast
import tomllib
from collections import defaultdict
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PACKAGES = ROOT / "packages"
PREFIX = "localforge-"

# Modulos de terceros que cada subproyecto puede declarar.
EXTERNAS = {"pydantic", "httpx"}


def subproyectos() -> list[str]:
    return sorted(p.name for p in PACKAGES.iterdir() if (p / "pyproject.toml").is_file())


def manifiesto(pkg: str) -> dict:
    return tomllib.loads((PACKAGES / pkg / "pyproject.toml").read_text(encoding="utf-8"))


def declaradas(pkg: str) -> set[str]:
    """Los OTROS subproyectos que `pkg` dice necesitar."""
    deps = manifiesto(pkg)["project"].get("dependencies", [])
    return {
        d.split(">")[0].split("=")[0].strip()[len(PREFIX):]
        for d in deps
        if d.startswith(PREFIX)
    }


def dueno_de(modulo: str) -> str | None:
    """A que subproyecto pertenece `localforge.<modulo>`."""
    for pkg in subproyectos():
        base = PACKAGES / pkg / "src" / "localforge"
        if (base / modulo).is_dir() or (base / f"{modulo}.py").is_file():
            return pkg
    return None


def importadas(pkg: str) -> dict[str, set[str]]:
    """Los subproyectos que `pkg` importa de verdad, con quien los importa."""
    out: dict[str, set[str]] = defaultdict(set)
    for f in (PACKAGES / pkg / "src").rglob("*.py"):
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                mods = [node.module]
            elif isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            else:
                continue
            for m in mods:
                if not m.startswith("localforge."):
                    continue
                otro = dueno_de(m.split(".")[1])
                if otro and otro != pkg:
                    out[otro].add(f.name)
    return out


# --- la propiedad central ---------------------------------------------------


@pytest.mark.parametrize("pkg", subproyectos())
def test_declara_todo_lo_que_importa(pkg: str) -> None:
    """Un import no declarado es el bug que tuvimos. Acá es un error."""
    usadas = importadas(pkg)
    faltan = {k: sorted(v) for k, v in usadas.items() if k not in declaradas(pkg)}
    assert not faltan, (
        f"'{pkg}' importa subproyectos que no declara en su pyproject.toml: "
        + ", ".join(f"{k} (desde {', '.join(v)})" for k, v in faltan.items())
    )


@pytest.mark.parametrize("pkg", subproyectos())
def test_no_declara_lo_que_no_usa(pkg: str) -> None:
    """Una dependencia muerta miente sobre el grafo y arrastra instalaciones."""
    sobran = declaradas(pkg) - set(importadas(pkg))
    assert not sobran, f"'{pkg}' declara pero no importa: {sorted(sobran)}"


def test_el_grafo_no_tiene_ciclos() -> None:
    grafo = {p: declaradas(p) for p in subproyectos()}

    def buscar(pkg: str, camino: list[str]) -> list[str] | None:
        if pkg in camino:
            return camino[camino.index(pkg):] + [pkg]
        for d in sorted(grafo.get(pkg, ())):
            if (c := buscar(d, camino + [pkg])):
                return c
        return None

    ciclos = [c for p in grafo if (c := buscar(p, []))]
    assert not ciclos, f"ciclos entre subproyectos: {ciclos}"


# --- forma del workspace ----------------------------------------------------


def test_la_raiz_no_es_un_paquete() -> None:
    """El codigo vive en packages/. La raíz sólo declara miembros y dev deps."""
    raiz = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert raiz["tool"]["uv"]["package"] is False
    assert raiz["tool"]["uv"]["workspace"]["members"] == ["packages/*"]


@pytest.mark.parametrize("pkg", subproyectos())
def test_cada_subproyecto_se_llama_igual_que_su_carpeta(pkg: str) -> None:
    assert manifiesto(pkg)["project"]["name"] == f"{PREFIX}{pkg}"


@pytest.mark.parametrize("pkg", subproyectos())
def test_cada_subproyecto_es_namespace(pkg: str) -> None:
    """Sin `namespace = true` los once no pueden montar bajo el mismo
    `localforge.`, y el primero que se instale tapa a los demas."""
    build = manifiesto(pkg)["tool"]["uv"]["build-backend"]
    assert build["namespace"] is True, f"'{pkg}' no declara namespace = true"


def test_no_hay_init_en_la_raiz_del_namespace() -> None:
    """Un `localforge/__init__.py` rompe PEP 420: el paquete deja de ser
    namespace y sólo se ve el del subproyecto que lo trae."""
    culpables = sorted(
        str(p.relative_to(ROOT))
        for p in PACKAGES.glob("*/src/localforge/__init__.py")
    )
    assert not culpables, f"__init__.py en la raiz del namespace: {culpables}"


@pytest.mark.parametrize("pkg", subproyectos())
def test_las_externas_son_las_conocidas(pkg: str) -> None:
    """El proyecto no usa frameworks de agentes. Una dependencia nueva tiene que
    ser una decision consciente, no algo que se cuela en un pyproject."""
    deps = manifiesto(pkg)["project"].get("dependencies", [])
    externas = {
        d.split(">")[0].split("=")[0].split("[")[0].strip()
        for d in deps
        if not d.startswith(PREFIX)
    }
    desconocidas = externas - EXTERNAS
    assert not desconocidas, f"'{pkg}' declara dependencias nuevas: {sorted(desconocidas)}"


def test_el_cli_es_el_unico_con_entrypoint() -> None:
    con_scripts = [p for p in subproyectos() if "scripts" in manifiesto(p)["project"]]
    assert con_scripts == ["cli"], f"entrypoints inesperados: {con_scripts}"
