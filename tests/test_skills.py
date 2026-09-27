"""Tests de skills (Fase 4).

La propiedad que define la fase: **el costo fijo por turno es una linea por
skill, no el cuerpo.** Si eso se rompe, el progressive disclosure no existe y las
skills son solo un prompt mas largo.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from localforge.harness.prompt import build_system_prompt
from localforge.skills import MAX_BODY_CHARS, discover_skills, disclosure_block, parse_skill
from localforge.tools import ToolError, default_registry
from localforge.tools.skill import LoadSkillArgs, LoadSkillTool

# Marcador deliberadamente improbable. La primera version de este test buscaba
# "pytest" y daba un falso positivo: el tmp_path de pytest vive en
# .../pytest-of-<usuario>/pytest-N/..., y el prompt incluye la RUTA RAIZ del repo.
# Un test de "esto no aparece" necesita una aguja que no pueda venir de otro lado.
MARCA_CUERPO = "XCUERPODESKILLX"
CUERPO = f"Usá siempre pytest y nunca unittest. {MARCA_CUERPO}\n\nLos tests van en tests/."


def write_skill(root: Path, name: str, description: str, body: str = CUERPO, where: str = ".localforge/skills") -> Path:
    d = root / where / name
    d.mkdir(parents=True, exist_ok=True)
    f = d / "SKILL.md"
    f.write_text(f"---\nname: {name}\ndescription: {description}\n---\n{body}\n", encoding="utf-8")
    return f


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    write_skill(tmp_path, "convenciones-de-test", "como se escriben los tests en este repo")
    write_skill(tmp_path, "estilo-de-commits", "formato de los mensajes de commit")
    return tmp_path


# --- parseo -----------------------------------------------------------------


def test_parse_lee_frontmatter_y_cuerpo(tmp_path: Path) -> None:
    f = write_skill(tmp_path, "x", "para algo")
    skill = parse_skill(f.read_text(encoding="utf-8"), f)
    assert skill is not None
    assert skill.name == "x" and skill.description == "para algo"
    assert "pytest" in skill.body


def test_sin_frontmatter_no_es_skill(tmp_path: Path) -> None:
    assert parse_skill("solo texto\n", tmp_path / "SKILL.md") is None


def test_sin_descripcion_se_ignora(tmp_path: Path) -> None:
    """Sin descripcion el modelo no puede decidir si cargarla: es inutil."""
    assert parse_skill("---\nname: x\n---\ncuerpo\n", tmp_path / "SKILL.md") is None


def test_un_cuerpo_gigante_se_trunca(tmp_path: Path) -> None:
    f = write_skill(tmp_path, "grande", "descripcion", body="x" * (MAX_BODY_CHARS + 5_000))
    skill = parse_skill(f.read_text(encoding="utf-8"), f)
    assert skill is not None and skill.truncated
    assert len(skill.body) < MAX_BODY_CHARS + 100


# --- descubrimiento ---------------------------------------------------------


def test_descubre_en_orden_estable(repo: Path) -> None:
    nombres = [s.name for s in discover_skills(repo)]
    assert nombres == ["convenciones-de-test", "estilo-de-commits"]


def test_sin_directorio_de_skills_devuelve_vacio(tmp_path: Path) -> None:
    assert discover_skills(tmp_path) == []


def test_tambien_lee_claude_skills(tmp_path: Path) -> None:
    write_skill(tmp_path, "ajena", "de otra herramienta", where=".claude/skills")
    assert [s.name for s in discover_skills(tmp_path)] == ["ajena"]


def test_localforge_tiene_prioridad_sobre_claude(tmp_path: Path) -> None:
    write_skill(tmp_path, "dup", "la de claude", body="cuerpo claude", where=".claude/skills")
    write_skill(tmp_path, "dup", "la nuestra", body="cuerpo nuestro")
    skills = discover_skills(tmp_path)
    assert len(skills) == 1 and skills[0].description == "la nuestra"


def test_una_skill_ilegible_no_rompe_el_descubrimiento(repo: Path) -> None:
    d = repo / ".localforge/skills/rota"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_bytes(b"\xff\xfe binario")
    assert len(discover_skills(repo)) == 2  # las dos buenas siguen


# --- progressive disclosure: la propiedad central ---------------------------


def test_el_prompt_lleva_la_descripcion_pero_NO_el_cuerpo(repo: Path) -> None:
    prompt = build_system_prompt(repo, default_registry(), discover_skills(repo))
    assert "convenciones-de-test" in prompt
    assert "como se escriben los tests" in prompt
    # Lo que NO tiene que estar: el cuerpo.
    assert MARCA_CUERPO not in prompt, "el cuerpo de la skill se filtro al prompt de cada turno"


def test_el_costo_fijo_es_una_linea_por_skill(repo: Path) -> None:
    sin = len(build_system_prompt(repo, default_registry(), []))
    con = len(build_system_prompt(repo, default_registry(), discover_skills(repo)))
    # Dos skills: el prompt crece por las dos lineas mas el encabezado, no por
    # los ~90 caracteres de cuerpo de cada una.
    assert con - sin < 400, f"crecio {con - sin} caracteres"


def test_sin_skills_el_bloque_esta_vacio(tmp_path: Path) -> None:
    assert disclosure_block([]) == ""
    prompt = build_system_prompt(tmp_path, default_registry(), [])
    assert "SKILLS" not in prompt


# --- la tool ----------------------------------------------------------------


async def test_load_skill_trae_el_cuerpo(repo: Path) -> None:
    out = await LoadSkillTool().run(repo, LoadSkillArgs(name="convenciones-de-test"))
    assert MARCA_CUERPO in out and "nunca unittest" in out


async def test_load_skill_inexistente_lista_las_validas(repo: Path) -> None:
    with pytest.raises(ToolError) as exc:
        await LoadSkillTool().run(repo, LoadSkillArgs(name="no-existe"))
    assert "convenciones-de-test" in str(exc.value)
    assert "estilo-de-commits" in str(exc.value)


async def test_load_skill_sin_skills_es_claro(tmp_path: Path) -> None:
    with pytest.raises(ToolError, match="no tiene skills"):
        await LoadSkillTool().run(tmp_path, LoadSkillArgs(name="x"))


def test_esta_registrada_y_permitida() -> None:
    from localforge.models import ToolCall
    from localforge.sandbox import Decision, default_policy

    assert "load_skill" in default_registry().names()
    verdict = default_policy().decide(ToolCall(id="c", name="load_skill", arguments={"name": "x"}))
    assert verdict.decision is Decision.ALLOW


# --- la capa de contexto ----------------------------------------------------


def test_la_capa_skills_deja_de_estar_ausente(repo: Path) -> None:
    from localforge.context import ContextBudget, ContextBuilder

    builder = ContextBuilder(ContextBudget(limit=32_768))
    con = builder.build(
        system=build_system_prompt(repo, default_registry(), discover_skills(repo)),
        task="x",
        messages=[],
        definitions=[],
    )
    capa = con.breakdown.layer("skills")
    assert capa is not None and capa.present and capa.tokens > 0


def test_sin_skills_la_capa_se_reporta_ausente(tmp_path: Path) -> None:
    from localforge.context import ContextBudget, ContextBuilder

    builder = ContextBuilder(ContextBudget(limit=32_768))
    con = builder.build(
        system=build_system_prompt(tmp_path, default_registry(), []),
        task="x",
        messages=[],
        definitions=[],
    )
    capa = con.breakdown.layer("skills")
    assert capa is not None and not capa.present
