from localforge.tools.base import Tool, ToolError, ToolExecutor, ToolRegistry
from localforge.tools.fs import ListFilesTool, ReadFileTool, safe_path
from localforge.tools.search import SearchCodeTool

__all__ = [
    "Tool", "ToolError", "ToolExecutor", "ToolRegistry",
    "ListFilesTool", "ReadFileTool", "SearchCodeTool", "safe_path",
    "default_registry",
]


def default_registry() -> ToolRegistry:
    """Las tools de solo lectura: orientarse, buscar, leer.

    Son las tres operaciones de una investigacion de codigo, y en ese orden:
    list_files da el mapa, search_code encuentra el lugar, read_file da el
    detalle. Sin la del medio, el paso de "encontrar" se hace adivinando.

    write_file y run_command llegan cuando exista el control de permisos.
    """
    return ToolRegistry([ListFilesTool(), SearchCodeTool(), ReadFileTool()])
