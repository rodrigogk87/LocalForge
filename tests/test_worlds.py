"""Cada mundo se lee sin que se filtren los siguientes.

Esa es la propiedad entera de `worlds/`, y es la razon por la que existe: leer el
codigo final para entender el Mundo 1 no funciona, porque abris `models.py` y
encontras ocho estados cuando en el Mundo 1 habia cinco -- y tres de ellos hablan
de un verifier que todavia no te explicaron.

Estos tests no corren los tests de cada mundo (eso es `pytest` dentro de cada
carpeta, y son 939 en total). Verifican las propiedades que hacen que la coleccion
sirva: que cada paso sea el anterior MAS UNA COSA, y que nada se adelante.

Los pasos 1 a 7 son fotos generadas desde la historia de git; el paso 8 es el
codigo vivo del proyecto, el unico que se edita a mano.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
WORLDS = ROOT / "worlds"

# Cuando aparece cada modulo, por numero de paso. La diagonal es la propiedad:
# un modulo no puede existir antes de su paso ni faltar despues.
APARECEN = {
    "context.py": 2,
    "search.py": 2,
    "state.py": 3,
    "verify.py": 3,
    "permissions.py": 4,
    "checkpoint.py": 5,
    "evals.py": 6,
    "skills.py": 7,
    "subagent.py": 8,
}

# Los estados que el Mundo 3 agrega a AgentStatus. Antes del paso 3 no pueden
# estar, porque no habia nada que verificara.
ESTADOS_W3 = ("WAITING_TOOL", "VERIFYING", "REPAIRING")


def pasos() -> list[Path]:
    return sorted(
        (d for d in WORLDS.iterdir() if d.is_dir() and (d / "pyproject.toml").is_file()),
        key=lambda d: int(d.name.split("-")[0]),
    )


def numero(d: Path) -> int:
    return int(d.name.split("-")[0])


def fuente(d: Path) -> str:
    """Todo el codigo de ese mundo, concatenado."""
    return "\n".join(f.read_text(encoding="utf-8") for f in sorted((d / "src").rglob("*.py")))


def test_estan_los_ocho() -> None:
    assert [numero(d) for d in pasos()] == list(range(1, 9))


# --- la propiedad central: nada se adelanta ---------------------------------


@pytest.mark.parametrize("modulo,desde", sorted(APARECEN.items()))
def test_cada_modulo_aparece_en_su_paso_y_no_antes(modulo: str, desde: int) -> None:
    for d in pasos():
        existe = any((d / "src").rglob(modulo))
        if numero(d) < desde:
            assert not existe, f"{d.name} tiene {modulo}, que es del paso {desde}"
        else:
            assert existe, f"{d.name} deberia tener {modulo} (aparece en el paso {desde})"


@pytest.mark.parametrize("estado", ESTADOS_W3)
def test_los_estados_de_verificacion_no_existen_antes_del_paso_3(estado: str) -> None:
    """El caso que motivó todo esto: leer el Mundo 1 y encontrar VERIFYING."""
    for d in pasos():
        models = (d / "src" / "localforge" / "models.py").read_text(encoding="utf-8")
        declarado = f'    {estado} = "' in models
        if numero(d) < 3:
            assert not declarado, f"{d.name} declara {estado}, que es del paso 3"
        else:
            assert declarado, f"{d.name} deberia declarar {estado}"


def test_el_paso_1_tiene_exactamente_cinco_estados() -> None:
    models = (pasos()[0] / "src" / "localforge" / "models.py").read_text(encoding="utf-8")
    bloque = models[models.index("class AgentStatus") : models.index("class StopReason")]
    valores = [l for l in bloque.splitlines() if l.startswith("    ") and ' = "' in l]
    assert len(valores) == 5, f"el Mundo 1 tenia cinco estados, hay {len(valores)}: {valores}"


# --- cada paso es el anterior mas una cosa ---------------------------------


def test_el_codigo_crece_paso_a_paso() -> None:
    tamanos = [(d.name, len(list((d / "src").rglob("*.py")))) for d in pasos()]
    for (a, na), (b, nb) in zip(tamanos, tamanos[1:]):
        assert nb >= na, f"{b} tiene menos archivos que {a} ({nb} < {na})"
    assert tamanos[-1][1] > tamanos[0][1], "el ultimo paso no crecio nada"


def test_los_tests_crecen_paso_a_paso() -> None:
    """Si un paso tuviera menos tests que el anterior, algo se perdio en el camino."""
    cuentas = [
        (d.name, sum(
            f.read_text(encoding="utf-8").count("\ndef test_")
            + f.read_text(encoding="utf-8").count("\nasync def test_")
            for f in (d / "tests").rglob("test_*.py")
        ))
        for d in pasos()
    ]
    for (a, na), (b, nb) in zip(cuentas, cuentas[1:]):
        assert nb >= na, f"{b} tiene menos tests que {a} ({nb} < {na})"


# --- cada mundo es un proyecto de verdad ------------------------------------


@pytest.mark.parametrize("d", pasos(), ids=lambda d: d.name)
def test_cada_mundo_es_un_proyecto_independiente(d: Path) -> None:
    cfg = tomllib.loads((d / "pyproject.toml").read_text(encoding="utf-8"))
    # Raiz de su propio workspace: si no, uv camina hacia arriba y se queja de
    # que esta carpeta esta dentro del workspace del repo sin ser miembro.
    assert cfg["tool"]["uv"]["workspace"]["members"] == []
    assert "pytest" in " ".join(cfg["project"]["optional-dependencies"]["dev"])
    assert (d / "src" / "localforge" / "models.py").is_file()
    assert list((d / "tests").glob("test_*.py"))


@pytest.mark.parametrize("d", pasos(), ids=lambda d: d.name)
def test_cada_mundo_tiene_su_propio_entrypoint(d: Path) -> None:
    """Nombres distintos (lfw1, lfw2, ...) para poder tener varios instalados."""
    cfg = tomllib.loads((d / "pyproject.toml").read_text(encoding="utf-8"))
    scripts = cfg["project"]["scripts"]
    assert len(scripts) == 1
    nombre = next(iter(scripts))
    assert nombre.startswith("lfw"), nombre
    assert scripts[nombre] == "localforge.cli:main"


def test_los_entrypoints_no_se_repiten() -> None:
    nombres = [
        next(iter(tomllib.loads((d / "pyproject.toml").read_text(encoding="utf-8"))["project"]["scripts"]))
        for d in pasos()
    ]
    assert len(set(nombres)) == len(nombres), f"entrypoints repetidos: {nombres}"


@pytest.mark.parametrize("d", pasos(), ids=lambda d: d.name)
def test_cada_mundo_tiene_readme_que_dice_de_donde_sale(d: Path) -> None:
    texto = (d / "README.md").read_text(encoding="utf-8")
    assert "build_worlds.py" in texto, "el README no dice que es generado"
    assert "uv sync" in texto and "uv run" in texto, "el README no dice como correrlo"


def test_hay_un_indice() -> None:
    texto = (WORLDS / "README.md").read_text(encoding="utf-8")
    for d in pasos():
        assert d.name in texto, f"el indice no menciona {d.name}"


def test_el_indice_dice_cual_es_foto_y_cual_es_vivo() -> None:
    """Los pasos 1-7 se regeneran; el 8 es donde se trabaja. Que quede dicho."""
    texto = (WORLDS / "README.md").read_text(encoding="utf-8")
    assert "regenera" in texto
    assert "vivo" in texto.lower()
