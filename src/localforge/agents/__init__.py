"""Multi-agente (W8): delegar para no pagar el contexto de la investigacion.

Este paquete esta ARRIBA de harness en la jerarquia de dependencias: un
subagente construye un AgentHarness, y el harness no sabe que existen los
subagentes. Cuando subagent.py vivia dentro de harness/ esa flecha apuntaba en
las dos direcciones y habia que importar el loop de forma diferida.
"""

from localforge.agents.subagent import (
    MAX_DEPTH,
    SubagentArgs,
    SubagentTool,
    registry_with_subagents,
)

__all__ = ["SubagentTool", "SubagentArgs", "registry_with_subagents", "MAX_DEPTH"]
