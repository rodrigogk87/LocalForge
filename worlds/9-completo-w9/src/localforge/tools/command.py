"""run_command: la primera tool con efectos, y solo adentro de un sandbox.

Hasta aca todas las tools eran de lectura y el daño posible estaba acotado por
construccion. Esta corre comandos arbitrarios -- tests, linters, `python -c` --
y por eso es la tool que la regla del proyecto bloqueaba: **"run_command no se
agrega sin sandbox"**.

Las capas, en el orden en que actuan:

1. **Registro.** `registry_with_sandbox()` solo la agrega si el sandbox esta
   disponible. Sin Docker, para el modelo la tool no existe.
2. **Politica.** Cae en ASK (un humano ve cada comando). Y si el sandbox no
   esta, `command_policy()` le pone un DENY explicito delante.
3. **La tool misma.** No tiene camino para correr en el host: sin sandbox,
   levanta ToolError. Las capas 1 y 2 pueden saltearse armando el registry a
   mano; esta no.

Un exit code distinto de cero NO es un fallo de la tool: "los tests fallaron" es
un resultado legitimo que el modelo tiene que leer. Fallo de la tool es no haber
podido correrlo (sin sandbox, timeout).
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from localforge.permissions import Decision, PermissionPolicy, Rule, default_policy
from localforge.sandbox import Sandbox
from localforge.tools.base import ToolError, ToolRegistry

# Cuanto de stdout/stderr vuelve. El executor trunca igual, pero cortar aca
# permite mostrar el FINAL de la salida, que es donde estan los errores.
TAIL_CHARS = 6_000


class RunCommandArgs(BaseModel):
    command: str = Field(
        min_length=1,
        max_length=2_000,
        description=(
            "Comando de shell a correr en la raiz del repo, dentro de un contenedor SIN red "
            "y con el repo en SOLO LECTURA. Sirve para correr tests, linters o scripts. "
            "No puede instalar paquetes (no hay red) ni modificar archivos del repo."
        ),
    )
    timeout_s: int = Field(default=20, ge=1, le=25, description="Segundos maximos.")


def _tail(text: str) -> str:
    if len(text) <= TAIL_CHARS:
        return text
    return f"[...se omiten los primeros {len(text) - TAIL_CHARS} caracteres]\n" + text[-TAIL_CHARS:]


class RunCommandTool:
    name = "run_command"
    description = (
        "Corre un comando de shell en un sandbox (contenedor sin red, repo en solo lectura, "
        "limites de CPU y memoria). Usalo para VERIFICAR comportamiento: correr los tests, "
        "ejecutar un script. Cada comando necesita aprobacion del usuario."
    )
    args_model = RunCommandArgs

    def __init__(self, sandbox: Sandbox | None) -> None:
        self.sandbox = sandbox

    async def run(self, workspace: Path, args: RunCommandArgs) -> str:
        if self.sandbox is None:
            raise ToolError("run_command no tiene sandbox configurado y no corre en el host.")
        ok, motivo = await self.sandbox.available()
        if not ok:
            raise ToolError(f"el sandbox no esta disponible ({motivo}); el comando no se corrio.")
        res = await self.sandbox.run(args.command, workspace, timeout_s=args.timeout_s)
        if res.timed_out:
            raise ToolError(
                f"el comando supero {args.timeout_s}s y se mato el contenedor. "
                "Acotalo (un solo archivo de tests, por ejemplo)."
            )
        parts = [f"$ {args.command}", f"[exit {res.exit_code} · {res.duration_ms / 1000:.1f}s]"]
        if res.stdout.strip():
            parts.append(_tail(res.stdout.rstrip()))
        if res.stderr.strip():
            parts.append("--- stderr ---\n" + _tail(res.stderr.rstrip()))
        if not res.stdout.strip() and not res.stderr.strip():
            parts.append("(sin salida)")
        return "\n".join(parts)


async def registry_with_sandbox(
    base: ToolRegistry, sandbox: Sandbox | None
) -> tuple[ToolRegistry, str]:
    """Agrega run_command SOLO si el sandbox esta disponible.

    Devuelve el registry y un texto para mostrar: por que esta o por que no.
    """
    if sandbox is None:
        return base, "sin sandbox: run_command no se agrega"
    ok, motivo = await sandbox.available()
    if not ok:
        return base, f"run_command deshabilitado: {motivo}"
    base.register(RunCommandTool(sandbox))
    return base, f"run_command en sandbox ({motivo})"


def command_policy(sandbox_ok: bool, base: PermissionPolicy | None = None) -> PermissionPolicy:
    """La politica con la regla de run_command delante.

    Con sandbox: ASK, con un motivo que dice que se va a correr y donde. Sin
    sandbox: DENY, aunque alguien haya registrado la tool igual.
    """
    policy = base or default_policy()
    if sandbox_ok:
        rule = Rule(
            tool="run_command",
            decision=Decision.ASK,
            reason="va a correr un comando (en un contenedor sin red, con el repo en solo lectura)",
        )
    else:
        rule = Rule(
            tool="run_command",
            decision=Decision.DENY,
            reason="no hay sandbox disponible, y run_command no corre fuera de uno",
        )
    policy.rules.insert(0, rule)
    return policy


__all__ = ["RunCommandTool", "RunCommandArgs", "registry_with_sandbox", "command_policy"]
