"""Permisos: ALLOW, ASK, DENY.

El harness ya tenia una asimetria fundamental: **el modelo propone, el harness
ejecuta**. Este modulo es lo que convierte esa asimetria en un control real, y
es el punto exacto donde se enchufa sin tocar el loop, tal como decia el
docstring de `tools/base.py` desde la Fase 1.

Tres decisiones que valen mas que el codigo:

1. **El default nunca es ALLOW.** Una tool que la politica no conoce se deniega.
   Es el mismo criterio que `ToolResult.success` sin default: en campos de
   seguridad, el default seguro es el pesimista. Un permiso que falla abierto
   convierte cada olvido en un agujero.

2. **Un DENY vuelve al modelo como ToolResult, no como excepcion.** El modelo
   lee "no tenes permiso para esto" y busca otro camino, igual que con cualquier
   otro error de tool. Matar al agente por un permiso denegado seria tratar una
   decision de politica como un fallo del sistema.

3. **ASK sin nadie a quien preguntar es DENY.** En modo no interactivo no hay
   humano que apruebe, y "no pude preguntar" no puede resolverse como "dale".

Vive en la raiz del paquete y no dentro de `harness/` por una razon de
dependencias: `tools/base.py` necesita los permisos para autorizar antes de
ejecutar, y `harness/` necesita a `tools/`. Si los permisos vivieran en
`harness/`, la flecha apuntaria en las dos direcciones y el import se volveria
circular -- lo fue, de hecho, hasta que se movio. Los permisos son una capa
transversal como `models`, no un detalle interno del harness.

Lo que este modulo NO es: un sandbox. No aisla el filesystem ni la red ni limita
CPU o memoria. Un permiso decide *si* se ejecuta; un sandbox contiene *lo que
pasa* cuando se ejecuta. Los dos hacen falta, y el sandbox es otro trabajo
(Docker, W5-C30).
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Sequence

from localforge.models import ToolCall


class Decision(StrEnum):
    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


# Archivos que no aportan a entender un repo y si filtran credenciales. El
# agente mete el contenido de lo que lee en el contexto, y el contexto viaja al
# modelo: leer un .env es exfiltrar secretos, aunque el modelo sea local y el
# dia que el provider sea remoto es literal.
SENSITIVE_GLOBS: tuple[str, ...] = (
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "id_rsa*",
    "id_ed25519*",
    "*.keystore",
    "credentials",
    "credentials.*",
    "secrets.*",
    "*secret*.json",
    "*.tfvars",
    ".npmrc",
    ".pypirc",
    ".netrc",
    ".git-credentials",
    "service-account*.json",
)


@dataclass(frozen=True)
class Rule:
    """Una regla de la politica.

    `tool` puede ser un nombre exacto o "*". `path_globs`, si esta, exige que
    el argumento `path` de la llamada matchee alguno -- asi una misma tool puede
    tener decisiones distintas segun sobre que archivo opere.
    """

    tool: str
    decision: Decision
    reason: str = ""
    path_globs: tuple[str, ...] = ()

    def matches(self, call: ToolCall) -> bool:
        if self.tool != "*" and self.tool != call.name:
            return False
        if not self.path_globs:
            return True
        path = str(call.arguments.get("path") or "")
        if not path:
            return False
        # Se matchea contra el nombre del archivo Y contra la ruta completa: un
        # glob como ".env" tiene que pegar en "config/.env", y uno como
        # "secrets/*" tiene que pegar por ruta.
        name = path.rsplit("/", 1)[-1]
        return any(
            fnmatch.fnmatch(name, g) or fnmatch.fnmatch(path, g) or fnmatch.fnmatch(path, f"*/{g}")
            for g in self.path_globs
        )


@dataclass(frozen=True)
class Verdict:
    decision: Decision
    reason: str = ""

    @property
    def allowed(self) -> bool:
        return self.decision is Decision.ALLOW


@dataclass
class PermissionPolicy:
    """La politica. Primera regla que matchea gana; si ninguna, el default.

    El orden importa y es responsabilidad de quien arma la lista: las reglas mas
    especificas (un DENY sobre `.env`) van antes que las generales (un ALLOW
    sobre `read_file`).
    """

    rules: list[Rule] = field(default_factory=list)
    # Fail closed. Cambiarlo a ALLOW es legitimo en un entorno de juguete, pero
    # tiene que ser una linea explicita que alguien escribio a mano.
    default: Decision = Decision.DENY
    default_reason: str = "la politica no conoce esta tool"

    def decide(self, call: ToolCall) -> Verdict:
        for rule in self.rules:
            if rule.matches(call):
                return Verdict(rule.decision, rule.reason)
        return Verdict(self.default, self.default_reason)


# ---------------------------------------------------------------------------
# Politica por defecto
# ---------------------------------------------------------------------------


def default_policy(extra_sensitive: Sequence[str] = ()) -> PermissionPolicy:
    """La politica de hoy: lectura permitida, secretos denegados, resto ASK.

    Se lee de arriba hacia abajo y el orden es el diseño:

    1. Nunca leer archivos sensibles, ni siquiera con las tools de lectura.
    2. Las tools de lectura estan permitidas sin preguntar.
    3. Cualquier otra tool -- incluidas las que todavia no existen, como
       `write_file` o `run_command` -- cae en ASK.

    El punto 3 es el importante: cuando alguien agregue una tool con efectos, va
    a caer en ASK por construccion, sin que haya que acordarse de agregarla aca.
    """
    globs = tuple(SENSITIVE_GLOBS) + tuple(extra_sensitive)
    return PermissionPolicy(
        rules=[
            Rule(
                tool="*",
                decision=Decision.DENY,
                reason=(
                    "es un archivo de credenciales. El contenido de lo que leo entra al "
                    "contexto, asi que leerlo seria filtrarlo. Si necesitas saber que "
                    "variables usa el proyecto, buscá dónde se leen en el código "
                    "(search_code) o mirá el .env.example si existe."
                ),
                path_globs=globs,
            ),
            Rule(tool="list_files", decision=Decision.ALLOW),
            Rule(tool="search_code", decision=Decision.ALLOW),
            Rule(tool="read_file", decision=Decision.ALLOW),
            Rule(tool="load_skill", decision=Decision.ALLOW),
        ],
        default=Decision.ASK,
        default_reason="es una tool con efectos y necesita aprobacion",
    )


def read_only_policy() -> PermissionPolicy:
    """Todo lo que no sea lectura se deniega, sin preguntar.

    Util para correr el agente desatendido sobre un repo que no es tuyo.
    """
    policy = default_policy()
    policy.default = Decision.DENY
    policy.default_reason = "esta corrida es de solo lectura"
    return policy


__all__ = [
    "Decision",
    "Rule",
    "Verdict",
    "PermissionPolicy",
    "default_policy",
    "read_only_policy",
    "SENSITIVE_GLOBS",
]
