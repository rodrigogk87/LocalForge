"""Skills: capacidades que el agente gana sin recompilarse.

Una skill es un directorio con un `SKILL.md`. El agente descubre las skills al
arrancar, ve **solo el nombre y la descripcion** de cada una, y carga el cuerpo
con una tool cuando decide que le hace falta.

Eso ultimo es **progressive disclosure** (W4-C23) y es toda la idea. Cinco skills
de 3000 palabras cada una serian 15000 palabras en el contexto de cada turno, se
usen o no. Con disclosure progresivo el costo fijo es una linea por skill, y el
cuerpo entra solo cuando se pide.

Es la misma economia que las tools: `ToolDefinition` manda el schema, no la
implementacion. Y encaja exactamente en el presupuesto de la Fase 2 -- `skills`
era una de las cuatro capas que `ContextBreakdown` reportaba con `present=False`.
Ahora puede tener un numero.

Una skill NO es codigo que se ejecuta: es texto que entra al contexto. Por eso no
necesita permisos propios, y por eso tampoco puede hacer nada que el agente no
pudiera hacer ya. Lo que aporta es SABER COMO se hacen las cosas en este repo.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

# Donde se buscan. El primero es el nuestro; el segundo existe porque si alguien
# ya escribio skills para otra herramienta, releerlas es gratis.
SKILL_DIRS = (".localforge/skills", ".claude/skills")

SKILL_FILE = "SKILL.md"

# Un SKILL.md gigante derrota el proposito: si el cuerpo no entra en el
# presupuesto, cargarlo rompe el contexto en vez de ayudar.
MAX_BODY_CHARS = 20_000


@dataclass(frozen=True)
class Skill:
    """Una capacidad documentada.

    `name` y `description` son lo unico que el modelo ve siempre: tienen que
    alcanzar para decidir si cargarla. Una descripcion vaga ("utilidades") hace
    que la skill nunca se use, por mas buena que sea.
    """

    name: str
    description: str
    body: str
    path: Path

    @property
    def disclosure_line(self) -> str:
        """Lo que entra al contexto en cada turno. Una linea, y nada mas."""
        return f"- {self.name}: {self.description}"

    @property
    def truncated(self) -> bool:
        return len(self.body) >= MAX_BODY_CHARS


def parse_skill(text: str, path: Path) -> Skill | None:
    """Parsea un SKILL.md con frontmatter.

        ---
        name: nombre-corto
        description: cuando usar esto, en una linea
        ---
        el cuerpo, que se carga a demanda

    Sin dependencia de YAML: son pares `clave: valor` hasta el cierre. Aceptar
    YAML completo aca seria aceptar input arbitrario de un repo ajeno y parsearlo
    con una libreria que sabe construir objetos -- exactamente lo que no se
    quiere hacer con contenido no confiable.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None

    meta: dict[str, str] = {}
    body_start = len(lines)
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            body_start = i + 1
            break
        key, sep, value = line.partition(":")
        if sep:
            meta[key.strip().lower()] = value.strip()

    name = meta.get("name") or path.parent.name
    description = meta.get("description", "")
    if not description:
        # Sin descripcion el modelo no puede decidir si cargarla, asi que la
        # skill es inutil y es mejor ignorarla que ocupar una linea sin sentido.
        return None

    body = "\n".join(lines[body_start:]).strip()
    if len(body) > MAX_BODY_CHARS:
        body = body[:MAX_BODY_CHARS] + "\n\n[...skill truncada]"
    return Skill(name=name, description=description, body=body, path=path)


def discover_skills(workspace: Path) -> list[Skill]:
    """Busca skills en el repo. Orden estable, nombres unicos.

    Un fallo al leer una skill NO es un error del agente: se ignora esa skill y
    se sigue. Un SKILL.md mal escrito en un repo ajeno no puede impedir que el
    agente arranque.
    """
    root = workspace.resolve()
    found: dict[str, Skill] = {}

    for rel in SKILL_DIRS:
        base = root / rel
        if not base.is_dir():
            continue
        for skill_file in sorted(base.glob(f"*/{SKILL_FILE}")):
            try:
                text = skill_file.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            skill = parse_skill(text, skill_file)
            # El primero gana: .localforge/skills tiene prioridad sobre .claude.
            if skill and skill.name not in found:
                found[skill.name] = skill

    return [found[k] for k in sorted(found)]


def disclosure_block(skills: list[Skill]) -> str:
    """El bloque que va al system prompt. Vacio si no hay skills.

    Vacio y no "no hay skills disponibles": una linea que dice que algo no existe
    igual cuesta tokens en cada turno.
    """
    if not skills:
        return ""
    lines = [
        "SKILLS DISPONIBLES (convenciones de este repositorio).",
        "Ves solo el nombre y para que sirve. Si una aplica a tu tarea, cargala",
        "con load_skill antes de trabajar: puede cambiar como hay que hacer las cosas aca.",
        "",
    ]
    lines += [s.disclosure_line for s in skills]
    return "\n".join(lines)


__all__ = [
    "Skill",
    "parse_skill",
    "discover_skills",
    "disclosure_block",
    "SKILL_DIRS",
    "MAX_BODY_CHARS",
]
