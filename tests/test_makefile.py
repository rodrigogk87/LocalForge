"""El Makefile es el único lugar donde viven los comandos.

Por que existe. Los ocho mundos son proyectos separados, y sin un lugar central
usar el repo era: entrar a la carpeta, acordarse de `uv sync`, acordarse del
nombre del entrypoint (`lfw5` para el paso 4, porque ese paso es el Mundo 5), y
exportar el modelo a mano. Cuatro cosas para recordar por comando.

Y ahi esta la otra mitad: **la configuracion del provider tambien se centraliza
aca.** El Makefile incluye el `.env` de la raiz y lo EXPORTA, asi los ocho mundos
ven el mismo modelo. Importa porque el Mundo 1 no sabe leer un `.env` -- esa
feature llego en el Mundo 2 -- y sin el export quedaba pidiendo el default
`qwen3:14b`, que es el modelo de otra maquina.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
MAKEFILE = ROOT / "Makefile"

# Los targets que tienen que existir: si se renombra uno, la doc queda mintiendo.
TARGETS = (
    "help", "worlds", "setup", "setup-all", "test", "test-all",
    "health", "ask", "eval", "runs", "resume", "submit", "worker", "jobs",
    "lab-context", "lab-harness", "lab-verify",
    "repo-test", "docs", "docs-check", "build", "check", "clean",
)


def texto() -> str:
    return MAKEFILE.read_text(encoding="utf-8")


def make(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["make", *args], cwd=ROOT, capture_output=True, text=True, timeout=120
    )


needs_make = pytest.mark.skipif(shutil.which("make") is None, reason="no hay make")


def test_existe_el_makefile() -> None:
    assert MAKEFILE.is_file()


@pytest.mark.parametrize("target", TARGETS)
def test_cada_target_esta_declarado(target: str) -> None:
    assert re.search(rf"^{re.escape(target)}:", texto(), re.M), f"falta el target '{target}'"


@pytest.mark.parametrize("target", TARGETS)
def test_cada_target_esta_documentado_en_la_ayuda(target: str) -> None:
    """`make` sin argumentos lista los targets leyendo los comentarios `## `.
    Un target sin su linea `## ` existe pero es invisible."""
    assert re.search(rf"^## {re.escape(target)}:", texto(), re.M), (
        f"'{target}' no tiene su linea '## {target}: ...' y no aparece en la ayuda"
    )


def test_el_env_se_incluye_y_se_exporta() -> None:
    """Sin el `export`, las variables serian de make y no llegarian al proceso
    hijo -- que es justo lo que hacia que el Mundo 1 pidiera qwen3:14b."""
    t = texto()
    assert "include .env" in t, "el Makefile no incluye el .env de la raiz"
    assert re.search(r"^export$", t, re.M), "incluye el .env pero no lo exporta"
    # Y tolera que no exista: un clone limpio no tiene .env.
    assert "wildcard .env" in t, "el include tiene que ser condicional"


def test_el_entrypoint_se_deriva_del_nombre_de_la_carpeta() -> None:
    """`worlds/4-sandbox-w5` -> `lfw5`. El paso 4 es el Mundo 5, asi que el
    comando NO se puede derivar del numero de paso."""
    assert "CMD := lf$(lastword $(subst -, ,$(notdir $(WORLD_DIR))))" in texto()


@needs_make
def test_la_ayuda_corre_y_lista_los_targets() -> None:
    proc = make()
    assert proc.returncode == 0, proc.stderr
    faltan = [t for t in TARGETS if f"make {t}" not in proc.stdout]
    assert not faltan, f"la ayuda no menciona: {faltan}"


@needs_make
def test_la_ayuda_dice_que_mundo_y_que_modelo() -> None:
    """Es la forma mas rapida de saber sobre que estas trabajando."""
    proc = make()
    assert "WORLD=" in proc.stdout
    assert "modelo:" in proc.stdout


@needs_make
def test_worlds_lista_los_nueve_con_su_comando() -> None:
    proc = make("worlds")
    assert proc.returncode == 0, proc.stderr
    for paso in range(1, 10):
        assert re.search(rf"^\s+{paso}\s", proc.stdout, re.M), f"falta el paso {paso}"
    # El comando de cada mundo, que es lo que no se puede adivinar.
    for pp in (ROOT / "worlds").glob("*/pyproject.toml"):
        cmd = next(iter(tomllib.loads(pp.read_text(encoding="utf-8"))["project"]["scripts"]))
        assert cmd in proc.stdout, f"'{cmd}' no aparece en `make worlds`"


@needs_make
def test_un_mundo_inexistente_falla_con_un_mensaje_util() -> None:
    proc = make("test", "WORLD=99")
    assert proc.returncode != 0
    assert "make worlds" in proc.stdout + proc.stderr, "el error no dice como listar los mundos"


def test_la_doc_usa_el_makefile() -> None:
    """Si el README sigue mandando a correr los comandos a mano, el Makefile no
    centraliza nada."""
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "make " in readme, "el README no menciona el Makefile"


def test_los_targets_que_la_doc_menciona_existen() -> None:
    """La contracara del test de comandos: si la doc dice `make foo` y ese target
    no existe, el lector copia y no pasa nada. Es el mismo error que tuvimos con
    `uv run localforge`, una capa mas arriba."""
    declarados = set(re.findall(r"^([a-z][\w-]*):", texto(), re.M))
    for doc in ("README.md", "PROJECT_STATE.md", "worlds/README.md",
                "docs/GUIA.md", "docs/guia-web.html"):
        contenido = (ROOT / doc).read_text(encoding="utf-8")
        usados = set(re.findall(r"\bmake ([a-z][\w-]*)", contenido))
        # "make worlds" y demas; se descartan palabras que no son targets.
        desconocidos = sorted(u for u in usados if u not in declarados)
        assert not desconocidos, f"{doc} menciona targets que no existen: {desconocidos}"


def test_el_makefile_no_repite_la_configuracion_del_modelo() -> None:
    """Si el Makefile hardcodeara un modelo, la configuracion dejaria de estar en
    un solo lugar -- que es justo lo que vino a resolver."""
    t = texto()
    assert "qwen3" not in t and "gemma" not in t, (
        "el Makefile nombra un modelo concreto: la config va en el .env"
    )

