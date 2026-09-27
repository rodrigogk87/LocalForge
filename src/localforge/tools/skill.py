"""load_skill: la otra mitad del progressive disclosure.

El system prompt lista nombre y descripcion de cada skill. Esta tool devuelve el
cuerpo. La separacion es el punto: el costo fijo por turno es una linea, y el
cuerpo entra una sola vez, cuando el modelo decide que le hace falta.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from localforge.skills import discover_skills
from localforge.tools.base import ToolError


class LoadSkillArgs(BaseModel):
    name: str = Field(
        min_length=1,
        description="Nombre de la skill, exactamente como aparece en la lista del system prompt.",
    )


class LoadSkillTool:
    name = "load_skill"
    description = (
        "Carga el contenido completo de una skill del repositorio. El system prompt lista las "
        "skills disponibles con su nombre y para que sirven; esta tool trae las instrucciones. "
        "Usala cuando una skill aplique a tu tarea, ANTES de trabajar: puede cambiar como hay "
        "que hacer las cosas en este repositorio."
    )
    args_model = LoadSkillArgs

    async def run(self, workspace: Path, args: LoadSkillArgs) -> str:
        skills = discover_skills(workspace)
        if not skills:
            raise ToolError("este repositorio no tiene skills definidas.")

        match = next((s for s in skills if s.name == args.name), None)
        if match is None:
            # El modelo escribe el nombre: equivocarse es esperable. Decirle
            # cuales SI existen sube muchisimo la tasa de correccion, igual que
            # con las tools desconocidas.
            disponibles = ", ".join(s.name for s in skills)
            raise ToolError(f"no existe la skill '{args.name}'. Disponibles: {disponibles}")

        head = f"# skill: {match.name}\n{match.description}\n"
        if match.truncated:
            head += "\n[esta skill esta truncada: era mas larga que el limite]\n"
        return f"{head}\n{match.body}"


__all__ = ["LoadSkillTool", "LoadSkillArgs"]
