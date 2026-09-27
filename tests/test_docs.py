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
    """¿Existe `localforge/harness/loop.py` en alguno de los subproyectos?

    Las guias citan la ruta de IMPORT y no la del filesystem, porque es mas
    corta y no cambia si un subproyecto se renombra. La real vive en
    `packages/<sub>/src/`, y este helper la busca ahi.
    """
    return any((ROOT / "packages").glob(f"*/src/{ruta_de_import}"))


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
