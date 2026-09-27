"""Skills (W4): capacidades que el agente gana sin recompilarse."""

from localforge.skills.discovery import (
    MAX_BODY_CHARS,
    SKILL_DIRS,
    Skill,
    discover_skills,
    disclosure_block,
    parse_skill,
)

__all__ = [
    "Skill", "parse_skill", "discover_skills", "disclosure_block",
    "SKILL_DIRS", "MAX_BODY_CHARS",
]
