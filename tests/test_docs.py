"""Las guias no pueden mentir sobre el codigo.

Este archivo existe por un problema concreto que se sufrio: las guias citaban
"lineas 27-67" de `models.py`, el codigo crecio en las fases 2 a 8, los archivos
se movieron de paquete, y **40 referencias quedaron apuntando a otro lado** sin
que nada avisara. Alguien que seguia el proyecto desde `main` abria la linea 97
esperando `ToolCall` y encontraba cualquier cosa.

Un numero de linea escrito a mano en un documento es un dato duplicado: vive en
el documento y en el codigo, y nada los ata. Estos tests son lo que los ata.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
SCRIPT = ROOT / "scripts" / "sync_code_refs.py"
TABLE = DOCS / "code-refs.json"


def test_las_referencias_al_codigo_estan_en_sincronia() -> None:
    """El test que impide que esto vuelva a podrirse.

    Si falla, el mensaje dice exactamente que referencia quedo vieja y cual es
    el numero correcto. Se arregla corriendo el script sin --check.
    """
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--check"], capture_output=True, text=True, cwd=ROOT
    )
    assert proc.returncode == 0, (
        "las guias citan lineas que ya no corresponden.\n"
        "Corré: uv run python scripts/sync_code_refs.py\n\n" + proc.stdout
    )


def test_todo_archivo_citado_en_la_tabla_existe() -> None:
    refs = json.loads(TABLE.read_text(encoding="utf-8"))["refs"]
    faltan = [(k, r["src"]) for k, r in refs.items() if not (ROOT / r["src"]).is_file()]
    assert not faltan, f"referencias a archivos que no existen: {faltan}"


def test_todo_anclaje_se_encuentra_en_su_archivo() -> None:
    """Un anclaje que no aparece es peor que un numero viejo: el documento cita
    algo que ya no existe, y eso no se arregla ajustando un numero."""
    refs = json.loads(TABLE.read_text(encoding="utf-8"))["refs"]
    perdidos: list[str] = []
    for key, ref in refs.items():
        texto = (ROOT / ref["src"]).read_text(encoding="utf-8")
        for anchor in ref.get("pair", []) + [ref[k] for k in ("from", "to") if k in ref]:
            if anchor not in texto:
                perdidos.append(f"{key}: '{anchor}' no esta en {ref['src']}")
    assert not perdidos, "anclajes perdidos:\n  " + "\n  ".join(perdidos)


def _existe(ruta_de_import: str) -> bool:
    """¿Existe `localforge/harness/loop.py` en alguno de los mundos?

    Las guias citan la ruta de IMPORT y no la del filesystem, porque es la misma
    en los ocho mundos. La real vive en `worlds/<paso>/src/`, y alcanza con que
    exista en UNO: la seccion del Mundo 1 cita archivos del Mundo 1, y el Mundo 6
    puede citar uno que en el 1 todavia no existia.
    """
    return any((ROOT / "worlds").glob(f"*/src/{ruta_de_import}"))


@pytest.mark.parametrize("doc", ["guia-web.html", "GUIA.md"])
def test_toda_ruta_de_codigo_citada_en_las_guias_existe(doc: str) -> None:
    """Atrapa la mudanza de un archivo, que es lo que mas duele: un numero viejo
    apunta a otro lado, una ruta vieja no apunta a ningun lado."""
    texto = (DOCS / doc).read_text(encoding="utf-8")
    rutas = set(re.findall(r"localforge/[\w/]+\.py", texto))
    faltan = sorted(r for r in rutas if not _existe(r))
    assert not faltan, f"{doc} cita archivos que no existen: {faltan}"


# Los ocho mundos por su nombre. Se chequea el TITULO y no "MUNDO N" porque los
# dos documentos los presentan distinto: la version web tiene una seccion por
# mundo, y GUIA.md desarrolla 1 y 2 y delega 3-8 a la web con una tabla. El
# contrato compartido es que ninguno de los ocho puede faltar.
MUNDOS = (
    "Agent Foundations",
    "Context Engineering",
    "Harness Engineering",
    "Skills",
    "Sandbox",
    "Durable",
    "Evals",
    "Multi-Agent",
)


@pytest.mark.parametrize("doc", ["guia-web.html", "GUIA.md"])
def test_las_guias_cubren_los_ocho_mundos(doc: str) -> None:
    texto = (DOCS / doc).read_text(encoding="utf-8").lower()
    faltan = [m for m in MUNDOS if m.lower() not in texto]
    assert not faltan, f"{doc} no cubre: {faltan}"


def test_la_web_tiene_una_seccion_por_mundo() -> None:
    """La version web si desarrolla los ocho, y ese es su contrato propio."""
    texto = (DOCS / "guia-web.html").read_text(encoding="utf-8")
    faltan = [n for n in range(1, 9) if f"MUNDO {n}" not in texto]
    assert not faltan, f"la guia web no tiene seccion para los mundos: {faltan}"


def test_el_html_esta_bien_formado() -> None:
    """Un </div> de mas rompe el layout entero y no se ve hasta abrirlo."""
    from html.parser import HTMLParser

    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input",
            "link", "meta", "param", "source", "track", "wbr"}

    class Check(HTMLParser):
        def __init__(self) -> None:
            super().__init__(convert_charrefs=True)
            self.stack: list[tuple[str, int]] = []
            self.errors: list[str] = []

        def handle_starttag(self, tag: str, attrs) -> None:  # noqa: ANN001
            if tag not in VOID:
                self.stack.append((tag, self.getpos()[0]))

        def handle_endtag(self, tag: str) -> None:
            if tag in VOID:
                return
            if not self.stack:
                self.errors.append(f"</{tag}> sin abrir en linea {self.getpos()[0]}")
            elif self.stack[-1][0] != tag:
                abierto, linea = self.stack[-1]
                self.errors.append(f"</{tag}> cierra <{abierto}> abierto en linea {linea}")
            else:
                self.stack.pop()

    parser = Check()
    parser.feed((DOCS / "guia-web.html").read_text(encoding="utf-8"))
    assert not parser.errors, "anidado roto:\n  " + "\n  ".join(parser.errors)
    assert not parser.stack, f"tags sin cerrar: {parser.stack}"


def test_no_hay_anclas_internas_rotas() -> None:
    texto = (DOCS / "guia-web.html").read_text(encoding="utf-8")
    ids = set(re.findall(r'\bid="([^"]+)"', texto))
    hrefs = {h for h in re.findall(r'href="#([^"]+)"', texto) if h}
    rotas = sorted(hrefs - ids)
    assert not rotas, f"links internos que no llevan a ningun lado: {rotas}"


# --- los comandos que la doc dice que corras tienen que existir --------------
#
# Esto existe porque la doc quedo diciendo `uv run localforge ask ...` despues de
# que el repo pasara a ser ocho proyectos y ese entrypoint dejara de existir.
# Un lector copiaba el comando de la primera pagina y no funcionaba nada.

import tomllib  # noqa: E402

# Lo que no es un entrypoint de un mundo pero es legitimo en un `uv run`.
HERRAMIENTAS = {"pytest", "python", "ruff", "uv"}


def entrypoints() -> set[str]:
    """Los comandos que los proyectos de worlds/ declaran de verdad."""
    nombres: set[str] = set()
    for pp in (ROOT / "worlds").glob("*/pyproject.toml"):
        cfg = tomllib.loads(pp.read_text(encoding="utf-8"))
        nombres |= set(cfg["project"].get("scripts", {}))
    return nombres


@pytest.mark.parametrize("doc", ["guia-web.html", "GUIA.md"])
def test_los_comandos_de_las_guias_existen(doc: str) -> None:
    texto = (DOCS / doc).read_text(encoding="utf-8")
    # `uv run <algo>`, salteando los flags (`uv run --no-sync pytest`).
    usados = set()
    for m in re.finditer(r"uv run ((?:--[\w-]+\s+)*)([\w.-]+)", texto):
        usados.add(m.group(2))
    validos = entrypoints() | HERRAMIENTAS
    desconocidos = sorted(usados - validos)
    assert not desconocidos, (
        f"{doc} dice correr comandos que no existen: {desconocidos}. "
        f"Los entrypoints reales son: {sorted(entrypoints())}"
    )


def test_las_guias_no_mencionan_el_entrypoint_viejo() -> None:
    """`localforge` desaparecio cuando el repo paso a ser ocho proyectos."""
    for doc in ("guia-web.html", "GUIA.md"):
        texto = (DOCS / doc).read_text(encoding="utf-8")
        assert "uv run localforge" not in texto, f"{doc} todavia usa el comando `localforge`"


def test_las_rutas_de_ejemplo_apuntan_a_worlds() -> None:
    """Otra que quedo vieja: `cd ~/Desktop/LocalForge`, que ya no es donde vive."""
    for doc in ("guia-web.html", "GUIA.md"):
        texto = (DOCS / doc).read_text(encoding="utf-8")
        assert "~/Desktop/LocalForge" not in texto, f"{doc} tiene una ruta absoluta vieja"


# --- las cuentas que la guia afirma sobre el codigo de un mundo ---------------
#
# El 1.1 decia "abri el archivo y vas a ver MAS valores de los que estan aca, los
# agregaron mundos posteriores". Era cierto cuando habia un solo codebase, y
# quedo FALSO cuando cada mundo paso a ser su propio proyecto: el models.py del
# paso 1 tiene exactamente cinco estados y seis motivos. Tambien decia que el
# Mundo 2 agregaba un motivo, y en realidad los dos entran en el Mundo 3.
#
# Estos tests atan esas afirmaciones al codigo.

WORLDS = ROOT / "worlds"


def _valores(paso: int, enum: str, hasta: str) -> int:
    ruta = next((WORLDS / f"{paso}-*").parent.glob(f"{paso}-*/src/localforge/models.py"))
    texto = ruta.read_text(encoding="utf-8")
    bloque = texto[texto.index(f"class {enum}") :]
    bloque = bloque[: bloque.index(hasta)] if hasta in bloque else bloque
    return len(re.findall(r'^    [A-Z_]+ = "', bloque, re.M))


def test_el_paso_1_tiene_los_valores_que_la_guia_dice() -> None:
    """La guía afirma cinco estados y seis motivos. Si el código dice otra cosa,
    el lector cuenta y no coincide."""
    assert _valores(1, "AgentStatus", "class StopReason") == 5
    assert _valores(1, "FailureReason", "# ---") == 6


def test_el_crecimiento_de_los_enums_pasa_en_el_paso_3() -> None:
    """Y no en el 2, que es lo que la guía decía mal: los dos motivos nuevos y los
    tres estados entran juntos con el verifier."""
    assert _valores(2, "AgentStatus", "class StopReason") == 5, "el paso 2 no toca AgentStatus"
    assert _valores(2, "FailureReason", "# ---") == 6, "el paso 2 no toca FailureReason"
    assert _valores(3, "AgentStatus", "class StopReason") == 8, "el paso 3 suma tres estados"
    assert _valores(3, "FailureReason", "# ---") == 8, "el paso 3 suma dos motivos"


@pytest.mark.parametrize("doc", ["guia-web.html", "GUIA.md"])
def test_la_guia_no_manda_a_buscar_valores_que_no_estan(doc: str) -> None:
    """Regresion literal del texto que reportaste."""
    texto = (DOCS / doc).read_text(encoding="utf-8")
    assert "más valores de los que están acá" not in texto, (
        f"{doc} le dice al lector que va a ver valores que en ese mundo no existen"
    )


@pytest.mark.parametrize("doc", ["guia-web.html", "GUIA.md"])
def test_cada_mundo_linkea_su_codigo_en_github(doc: str) -> None:
    """Lo que el estudiante si necesita del andamiaje: donde esta el codigo."""
    texto = (DOCS / doc).read_text(encoding="utf-8")
    carpetas = sorted(d.name for d in WORLDS.iterdir() if (d / "pyproject.toml").is_file())
    faltan = [c for c in carpetas if f"worlds/{c}" not in texto]
    assert not faltan, f"{doc} no linkea el codigo de: {faltan}"

