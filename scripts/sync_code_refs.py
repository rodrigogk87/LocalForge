#!/usr/bin/env python3
"""Mantiene sincronizadas las referencias al codigo que hacen las guias.

El problema que resuelve, y que se sufrio de verdad: las guias citaban "lineas
27-67" de `models.py`. Despues el codigo crecio, los archivos se movieron, y
**todas esas referencias quedaron apuntando a otro lado** sin que nada avisara.
Alguien que seguia el proyecto abria la linea 97 esperando `ToolCall` y encontraba
cualquier cosa.

Cada ref apunta al proyecto del MUNDO que la guia esta explicando: la seccion del
Mundo 1 cita `worlds/1-foundations-w1`, asi los numeros son los del archivo que el
lector va a abrir de verdad -- no los de una copia final donde todo esta fusionado.

La causa de fondo es que un numero de linea escrito a mano es un dato duplicado:
vive en el documento y en el codigo, y nada los ata. Este script invierte eso.
`docs/code-refs.json` dice QUE simbolo se cita; el numero de linea se calcula.

    python scripts/sync_code_refs.py            # escribe los numeros
    python scripts/sync_code_refs.py --check    # falla si estan desfasados

El modo --check corre en los tests (tests/test_docs.py), asi que esto no puede
volver a podrirse en silencio: la proxima vez que se mueva codigo, el test falla
y dice exactamente que referencia quedo vieja.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TABLE = ROOT / "docs" / "code-refs.json"


class Unresolved(Exception):
    """Un anclaje no se encontro en el codigo.

    Es un error y no un warning: significa que el documento cita algo que ya no
    existe, y eso no se arregla ajustando un numero.
    """


def load() -> dict:
    return json.loads(TABLE.read_text(encoding="utf-8"))


def find_line(src: Path, needle: str) -> int:
    """Primera linea que contiene `needle`, 1-indexed."""
    if not src.is_file():
        raise Unresolved(f"el archivo '{src}' no existe")
    for n, line in enumerate(src.read_text(encoding="utf-8").splitlines(), start=1):
        if needle in line:
            return n
    raise Unresolved(f"'{needle}' no aparece en {src.name}")


def render(ref: dict) -> str:
    """El texto visible que le corresponde hoy a una referencia."""
    src = ROOT / ref["src"]
    if "pair" in ref:
        a, b = (find_line(src, p) for p in ref["pair"])
        return f"líneas {a} y {b}"
    start = find_line(src, ref["from"])
    if "to" in ref:
        end = find_line(src, ref["to"])
        if end < start:
            raise Unresolved(f"el ancla final de {ref['src']} quedo antes del inicio")
        return f"líneas {start}-{end}"
    return f"línea {start}"


def sync_html(text: str, mapping: dict, refs: dict) -> tuple[str, list[str]]:
    """Reescribe el <span class="ln"> de cada <h3>N.M ...</h3>."""
    problemas: list[str] = []

    def repl(m: re.Match) -> str:
        num, medio, actual = m.group(1), m.group(2), m.group(3)
        key = mapping.get(num)
        if key is None:
            return m.group(0)
        esperado = render(refs[key])
        if actual != esperado:
            problemas.append(f'guia-web.html {num}: dice "{actual}", corresponde "{esperado}"')
        return f'<h3>{num}{medio}<span class="ln">{esperado}</span>'

    return re.sub(r'<h3>(\d+\.\d+)(.*?)<span class="ln">([^<]+)</span>', repl, text), problemas


def sync_md(text: str, mapping: dict, refs: dict) -> tuple[str, list[str]]:
    """Reescribe el parentesis de cada `### N.M — titulo (lineas ...)`."""
    problemas: list[str] = []

    def repl(m: re.Match) -> str:
        num, titulo, actual = m.group(1), m.group(2), m.group(3)
        key = mapping.get(num)
        if key is None:
            return m.group(0)
        esperado = render(refs[key])
        if actual != esperado:
            problemas.append(f'GUIA.md {num}: dice "{actual}", corresponde "{esperado}"')
        return f"### {num} —{titulo}({esperado})"

    return re.sub(r"^### (\d+\.\d+) —(.*?)\((líneas? [^)]+)\)$", repl, text, flags=re.M), problemas


def sync_badges(text: str, archivos: dict[str, str]) -> tuple[str, list[str]]:
    """Actualiza los `· N líneas` que acompañan a cada archivo citado.

    `archivos` mapea lo que el documento MUESTRA (`localforge/models.py`) a la
    ruta real dentro del mundo que corresponde
    (`worlds/1-foundations-w1/src/localforge/models.py`).
    """
    problemas: list[str] = []
    for rel, ruta in archivos.items():
        real = len((ROOT / ruta).read_text(encoding="utf-8").splitlines())
        for envoltura in (f"<b>{rel}</b>", f"`{rel}`"):
            pat = re.compile(re.escape(envoltura) + r" · (\d+) líneas")
            for m in pat.finditer(text):
                if int(m.group(1)) != real:
                    problemas.append(f"{rel}: el badge dice {m.group(1)} líneas, son {real}")
            text = pat.sub(f"{envoltura} · {real} líneas", text)
    return text, problemas


# ---------------------------------------------------------------------------
# Numeros del hero: computados, no escritos a mano
# ---------------------------------------------------------------------------

def stats() -> dict[str, int]:
    """Los numeros que las guias afirman sobre si mismas y sobre el codigo.

    Estan aca por la misma razon que las lineas: escritos a mano se podren. El
    hero decia "4595 lineas de codigo" y "59 links a clases" cuando eran 4645 y
    56, porque el codigo crecio y los links cambiaron.
    """
    worlds = ROOT / "worlds"
    html = (ROOT / "docs" / "guia-web.html").read_text(encoding="utf-8")
    vivo = worlds / "8-multiagent-w8" / "src"
    return {
        "mundos": len([d for d in worlds.iterdir() if d.is_dir() and (d / "pyproject.toml").is_file()]),
        "líneas de código": sum(
            len(f.read_text(encoding="utf-8").splitlines()) for f in vivo.rglob("*.py")
        ),
        "links a clases": len(re.findall(r"academy-chi\.vercel\.app/#/lesson/", html)),
        "experimentos": len(set(re.findall(r"Experimento (\d+)", html))),
    }


def sync_stats(text: str, valores: dict[str, int]) -> tuple[str, list[str]]:
    """Reescribe los <div class="fact"> del hero."""
    problemas: list[str] = []
    for clave, real in valores.items():
        pat = re.compile(
            r'(<div class="fact"><div class="v">)(\d+)(</div><div class="k">'
            + re.escape(clave) + r"</div></div>)"
        )
        for m in pat.finditer(text):
            if int(m.group(2)) != real:
                problemas.append(f'hero "{clave}": dice {m.group(2)}, son {real}')
        text = pat.sub(lambda m: f"{m.group(1)}{real}{m.group(3)}", text)
    return text, problemas


def main() -> int:
    check = "--check" in sys.argv
    data = load()
    refs = data["refs"]
    problemas: list[str] = []

    # Toda ruta citada tiene que existir. Esto es lo que atrapa una mudanza de
    # archivo, que es peor que un numero de linea viejo: el numero apunta a otro
    # lado, la ruta no apunta a ningun lado.
    for key, ref in refs.items():
        if not (ROOT / ref["src"]).is_file():
            problemas.append(f"la ref '{key}' apunta a '{ref['src']}', que no existe")
    if problemas:
        print("rutas rotas en docs/code-refs.json:")
        for p in problemas:
            print(f"  - {p}")
        return 1

    valores = stats()
    for doc, syncer in (("guia-web.html", sync_html), ("GUIA.md", sync_md)):
        path = ROOT / "docs" / doc
        original = path.read_text(encoding="utf-8")
        text, probs = syncer(original, data[doc], refs)
        problemas += probs
        text, probs = sync_badges(text, data["file_badges"])
        problemas += probs
        if doc == "guia-web.html":
            text, probs = sync_stats(text, valores)
            problemas += probs
        if not check and text != original:
            path.write_text(text, encoding="utf-8")

    if problemas:
        cabeza = f"{len(problemas)} referencia(s) desfasada(s):" if check else f"{len(problemas)} corregida(s):"
        print(cabeza)
        for p in problemas:
            print(f"  - {p}")
        return 1 if check else 0

    print("referencias al codigo: en sincronia")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
