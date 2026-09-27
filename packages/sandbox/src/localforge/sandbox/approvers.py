"""Quien resuelve un ASK.

Vive aparte de la politica porque son dos preguntas distintas: la politica
decide QUE requiere aprobacion, el approver decide COMO se consigue. Cambiar de
consola a Slack no deberia tocar una sola regla.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from localforge.models import ToolCall


@runtime_checkable
class Approver(Protocol):
    """Quien resuelve un ASK. Async porque preguntarle a un humano bloquea."""

    async def approve(self, call: ToolCall, reason: str) -> bool: ...


class DenyingApprover:
    """El approver de modo no interactivo: dice no y explica por que.

    Es el default a proposito. Un ASK que nadie puede contestar no es un si.
    """

    async def approve(self, call: ToolCall, reason: str) -> bool:
        return False


class AutoApprover:
    """Aprueba todo. Solo para tests y para corridas desatendidas conscientes.

    Existe como clase con nombre explicito para que aparezca en el codigo que la
    usa: `AutoApprover()` en un diff se ve, un flag booleano no.
    """

    async def approve(self, call: ToolCall, reason: str) -> bool:
        return True

__all__ = ["Approver", "DenyingApprover", "AutoApprover"]
