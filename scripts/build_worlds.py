#!/usr/bin/env python3
"""Genera un proyecto Python independiente por mundo, desde la historia de git.

Por que existe. Leer el codigo final para entender el Mundo 1 no funciona: abris
`models.py` y encontras ocho estados cuando en el Mundo 1 habia cinco, y tres de
ellos hablan de un verifier que todavia no te explicaron. Los detalles de los
mundos posteriores tienen que ser **invisibles** mientras lees uno.

La solucion no es partir el codigo final -- eso es imposible, Python no deja
extender un enum y el loop importa de seis mundos. La solucion es que cada mundo
sea un proyecto aparte con el codigo **tal cual estaba** al cerrarlo.

Y no hace falta escribirlos a mano: cada mundo se cerro con un commit, asi que
los ocho snapshots ya existen. Este script los materializa.

**El orden es el de CONSTRUCCION, no el del roadmap**, y eso es a proposito: el
proyecto hizo el Mundo 5 (permisos) antes del 4 (skills) porque los permisos eran
prerequisito duro de cualquier tool con efectos. Cada carpeta se apoya en la
anterior de verdad; si las ordenara por numero de roadmap, el paso 4 tendria
skills y el 5 las perderia.

**Los pasos 8 y 9 no se regeneran.** Los pasos 1 a 7 son fotos de la historia y
se rehacen con un comando. El 8 tiene cambios hechos a mano despues del commit que
lo cerro (el flag --delegate, las anotaciones por mundo) y quedo congelado asi: es
la foto de los ocho mundos tal como se cerraron. El 9 es el codigo vivo: los ocho
mundos con lo que cada uno dejo pendiente, y el unico que se edita.

    python scripts/build_worlds.py            # genera worlds/
    python scripts/build_worlds.py --check    # verifica que esten al dia
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "worlds"


@dataclass(frozen=True)
class Mundo:
    paso: int
    slug: str
    mundo: int
    commit: str
    titulo: str
    propiedad: str
    agrega: str
    # Archivos a borrar del snapshot. Solo hace falta para el paso 7: los
    # Mundos 4 y 8 entraron en el MISMO commit, asi que para ver skills sin
    # subagentes hay que quitar el subagente.
    quitar: tuple[str, ...] = field(default=())
    # Los pasos que NO son fotos de un commit y no se regeneran: el 8 (congelado
    # con cambios a mano posteriores a su commit) y el 9 (el codigo vivo).
    # Regenerarlos borraria esos cambios.
    vivo: bool = False

    @property
    def dir(self) -> Path:
        return OUT / f"{self.paso}-{self.slug}-w{self.mundo}"

    @property
    def script(self) -> str:
        return f"lfw{self.mundo}"


MUNDOS = [
    Mundo(1, "foundations", 1, "7a3295d",
          "Python Agent Foundations",
          "el agente corre y termina",
          "models.py, providers/, tools/ (list_files, read_file), harness/loop.py"),
    Mundo(2, "context", 2, "49a0aaa",
          "Context Engineering",
          "y es barato y preciso",
          "context/ (estimador, capas, presupuesto, compactacion) y search_code"),
    Mundo(3, "harness", 3, "1c8c985",
          "Harness Engineering",
          "y esta estructurado y verifica",
          "state.py (transiciones prohibidas), verify.py y el repair loop"),
    Mundo(4, "sandbox", 5, "e2b48f9",
          "Sandbox Engineering",
          "y es seguro",
          "permisos ALLOW/ASK/DENY con fail-closed y aprobacion humana"),
    Mundo(5, "durable", 6, "24b24a5",
          "Durable Agents",
          "y sobrevive a un crash",
          "checkpoints atomicos por turno y resume idempotente"),
    Mundo(6, "evals", 7, "1808094",
          "Agent Evals",
          "y sabes si es bueno",
          "golden tasks, checks deterministas, taxonomia de fallos, comparador"),
    Mundo(7, "skills", 4, "5f62eb4",
          "Skills & Protocols",
          "y es extensible",
          "skills con progressive disclosure: una linea por skill en el prompt",
          quitar=("src/localforge/harness/subagent.py", "tests/test_subagent.py")),
    Mundo(8, "multiagent", 8, "5f62eb4",
          "Coding Agents & Multi-Agent",
          "y delega sin pagar el contexto",
          "subagentes con contexto aislado",
          vivo=True),
    Mundo(9, "completo", 9, "-",
          "Los ocho mundos, completos",
          "y cada mundo termina lo que dejo pendiente",
          "retrieval, planner, MCP, sandbox, queue y memoria, model-as-judge y worktrees",
          vivo=True),
]


def pyproject(m: Mundo, anterior: Mundo | None) -> str:
    desde = (
        f"# Se apoya en el paso {anterior.paso} (`{anterior.dir.name}`): este proyecto es ese,\n"
        f"# mas {m.agrega}.\n"
        if anterior
        else "# Es el primero: no se apoya en nada.\n"
    )
    return f'''# PASO {m.paso} — MUNDO {m.mundo}: {m.titulo}
#
# {m.propiedad.capitalize()}.
#
# Un proyecto aparte, completo y ejecutable, con el codigo TAL CUAL estaba al
# cerrar este mundo. **No hay nada de los mundos siguientes**, y eso es el punto:
# si abris models.py en el paso 1 vas a ver cinco estados, no ocho.
#
{desde}#
# Generado por scripts/build_worlds.py desde el commit {m.commit}. Su propio venv,
# su propio lockfile, su propio entrypoint:
#
#   cd worlds/{m.dir.name}
#   uv sync --extra dev
#   uv run pytest -q
#   uv run {m.script} ask . "explicame este proyecto"
[project]
name = "lfw{m.mundo}-{m.slug}"
version = "{m.paso}.0.0"
description = "Paso {m.paso} · Mundo {m.mundo} ({m.titulo}): {m.propiedad}."
requires-python = ">=3.12"
dependencies = [
    "pydantic>=2.9",
    "httpx>=0.27",
]

[project.optional-dependencies]
dev = ["pytest>=8.3", "pytest-asyncio>=0.24"]

[project.scripts]
{m.script} = "localforge.cli:main"

[build-system]
requires = ["uv_build>=0.12.10,<0.13.0"]
build-backend = "uv_build"

[tool.uv.build-backend]
module-name = "localforge"

# Raiz de su PROPIO workspace, sin miembros. Sin esto uv camina hacia arriba,
# encuentra el workspace del repo y se queja de que esta carpeta esta adentro
# pero no es miembro. Cada mundo tiene que ser independiente de verdad.
[tool.uv.workspace]
members = []

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
'''


def readme(m: Mundo, anterior: Mundo | None, siguiente: Mundo | None) -> str:
    nav = []
    if anterior:
        nav.append(f"← [paso {anterior.paso}: {anterior.titulo}](../{anterior.dir.name}/)")
    if siguiente:
        nav.append(f"[paso {siguiente.paso}: {siguiente.titulo}](../{siguiente.dir.name}/) →")
    return f'''# Paso {m.paso} · Mundo {m.mundo} — {m.titulo}

> **{m.propiedad.capitalize()}.**

Este directorio es un **proyecto Python completo e independiente** con el código tal cual estaba al
cerrar el Mundo {m.mundo}. No tiene nada de los mundos posteriores: lo que leas acá es lo que existía
en ese momento, sin adelantos.

## Qué agrega este paso

{m.agrega}

{"Se apoya en el **paso " + str(anterior.paso) + "** (" + anterior.titulo + "): este proyecto es ese, más lo de arriba." if anterior else "Es el primero de los ocho."}

## Correrlo

```bash
uv sync --extra dev
uv run pytest -q
uv run {m.script} health
uv run {m.script} ask . "explicame este proyecto"
```

Necesitás Ollama con un modelo que soporte tool calling (`ollama show <modelo>` tiene que listar
`tools` en *Capabilities*).

## Por qué el orden no es el del roadmap

Los pasos van en el orden en que el proyecto se **construyó**, que no es el orden de los números.
El Mundo 5 (permisos) se hizo antes del Mundo 4 (skills), porque los permisos eran prerequisito duro
de cualquier herramienta con efectos. Cada paso se apoya en el anterior de verdad.

{" · ".join(nav)}

---

{"*Este paso no se regenera. Los pasos 1 a 7 son fotos generadas desde la historia de git.*" if m.vivo else "*Generado por `scripts/build_worlds.py` desde el commit `" + m.commit + "`: es una **foto para leer**, no se edita a mano. El código vivo es el [paso 9](../9-completo-w9/).*"}
'''


def build(m: Mundo, anterior: Mundo | None, siguiente: Mundo | None) -> None:
    # Se borra el contenido pero NO el .venv: regenerar una foto no deberia
    # obligar a reinstalar sus dependencias.
    if m.dir.exists():
        for hijo in m.dir.iterdir():
            if hijo.name in (".venv", "uv.lock"):
                continue
            shutil.rmtree(hijo) if hijo.is_dir() else hijo.unlink()
    m.dir.mkdir(parents=True, exist_ok=True)

    # git archive saca el arbol de ese commit sin tocar el working tree.
    archive = subprocess.run(
        ["git", "archive", m.commit, "src", "tests"],
        cwd=ROOT, check=True, capture_output=True,
    ).stdout
    subprocess.run(["tar", "-x", "-C", str(m.dir)], input=archive, check=True)

    for rel in m.quitar:
        (m.dir / rel).unlink(missing_ok=True)

    (m.dir / "pyproject.toml").write_text(pyproject(m, anterior), encoding="utf-8")
    (m.dir / "README.md").write_text(readme(m, anterior, siguiente), encoding="utf-8")


def archivos(m: Mundo) -> int:
    """Cuenta los .py del proyecto, sin el venv."""
    return len([f for f in m.dir.rglob("*.py") if ".venv" not in f.parts])


def main() -> int:
    check = "--check" in sys.argv
    if check:
        faltan = [m.dir.name for m in MUNDOS if not (m.dir / "pyproject.toml").is_file()]
        if faltan:
            print("mundos sin generar:", faltan)
            return 1
        print(f"los {len(MUNDOS)} mundos estan generados")
        return 0

    OUT.mkdir(exist_ok=True)
    generados = 0
    for i, m in enumerate(MUNDOS):
        if m.vivo:
            print(f"  paso {m.paso}  w{m.mundo} {m.titulo:32} {archivos(m):2} archivos  no se regenera")
            continue
        build(m, MUNDOS[i - 1] if i else None, MUNDOS[i + 1] if i + 1 < len(MUNDOS) else None)
        generados += 1
        print(f"  paso {m.paso}  w{m.mundo} {m.titulo:32} {archivos(m):2} archivos  ({m.commit})")
    print(f"\n{generados} fotos regeneradas · el paso 9 es el codigo vivo")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
