from localforge.tools.base import Tool, ToolError, ToolExecutor, ToolRegistry
from localforge.tools.fs import ListFilesTool, ReadFileTool, safe_path
from localforge.tools.search import SearchCodeTool
from localforge.tools.skills import LoadSkillTool
from localforge.tools.retrieve import RetrieveTool

__all__ = [
    "Tool", "ToolError", "ToolExecutor", "ToolRegistry",
    "ListFilesTool", "ReadFileTool", "SearchCodeTool", "LoadSkillTool", "RetrieveTool", "safe_path",
    "default_registry",
]


def default_registry() -> ToolRegistry:
    """Las tools de solo lectura: orientarse, buscar, leer.

    Son las operaciones de una investigacion de codigo, y en ese orden:
    list_files da el mapa, search_code y retrieve encuentran el lugar, read_file
    da el detalle. Sin el paso del medio, "encontrar" se hace adivinando.

    Las tools con efectos (run_command, write_file) NO estan aca: run_command
    necesita sandbox y se suma con `registry_with_sandbox`; write_file solo
    existe dentro de un worktree descartable (ver harness/worktree.py).
    """
    return ToolRegistry(
        [ListFilesTool(), SearchCodeTool(), RetrieveTool(), ReadFileTool(), LoadSkillTool()]
    )
