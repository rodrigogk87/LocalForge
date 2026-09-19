from localforge.tools.base import Tool, ToolError, ToolExecutor, ToolRegistry
from localforge.tools.fs import ListFilesTool, ReadFileTool, safe_path

__all__ = [
    "Tool", "ToolError", "ToolExecutor", "ToolRegistry",
    "ListFilesTool", "ReadFileTool", "safe_path",
    "default_registry",
]


def default_registry() -> ToolRegistry:
    """Las tools de la Fase 1: solo lectura.

    write_file y run_command llegan cuando exista el control de permisos.
    """
    return ToolRegistry([ListFilesTool(), ReadFileTool()])
