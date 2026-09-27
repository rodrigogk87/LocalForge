"""Tools de filesystem: list_files y read_file.

Las dos comparten `safe_path`, que es el control de contencion mas barato que
existe. El sandbox real es de la Fase 5, pero validar rutas cuesta diez lineas
y ya evita que una tool salga del repositorio.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from localforge.tools.base import ToolError

# Directorios que nunca aportan senal y si mucho ruido de contexto.
IGNORED_DIRS = {
    ".git", ".hg", ".svn",
    "node_modules", ".venv", "venv", "__pycache__", ".pytest_cache", ".ruff_cache",
    "dist", "build", ".next", ".vite", "target", ".gradle", ".idea", ".vscode",
    ".mypy_cache", "coverage", ".turbo", ".cache",
}

IGNORED_SUFFIXES = {
    ".pyc", ".pyo", ".so", ".dll", ".dylib", ".exe", ".bin", ".lock",
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf", ".zip", ".gz", ".tar",
    ".woff", ".woff2", ".ttf", ".mp4", ".mp3",
}


def safe_path(workspace: Path, requested: str) -> Path:
    """Resuelve una ruta y verifica que caiga dentro del workspace.

    `resolve()` colapsa `..` y symlinks ANTES de comparar. Comparar strings sin
    resolver es el bug clasico: "/repo/../etc/passwd" empieza con "/repo".
    """
    root = workspace.resolve()
    candidate = (root / requested).resolve() if requested else root
    if candidate != root and root not in candidate.parents:
        raise ToolError(
            f"ruta fuera del workspace: '{requested}'. "
            "Solo se puede acceder a archivos dentro del repositorio."
        )
    return candidate


def _is_ignored(path: Path, root: Path) -> bool:
    rel_parts = path.relative_to(root).parts
    if any(part in IGNORED_DIRS for part in rel_parts):
        return True
    return path.suffix.lower() in IGNORED_SUFFIXES


# ---------------------------------------------------------------------------
# list_files
# ---------------------------------------------------------------------------


class ListFilesArgs(BaseModel):
    path: str = Field(
        default="",
        description="Subdirectorio relativo a la raiz del repo. Vacio o '.' para la raiz.",
    )
    max_depth: int = Field(
        default=3, ge=1, le=10, description="Profundidad maxima de subdirectorios a recorrer."
    )
    max_entries: int = Field(
        default=300, ge=1, le=2000, description="Cantidad maxima de archivos a devolver."
    )


class ListFilesTool:
    name = "list_files"
    description = (
        "Lista los archivos del repositorio en forma de arbol. Usar PRIMERO para orientarse "
        "antes de leer nada: es barato y muestra la estructura completa. Ignora "
        "automaticamente .git, node_modules, .venv, binarios y artefactos de build."
    )
    args_model = ListFilesArgs

    async def run(self, workspace: Path, args: ListFilesArgs) -> str:
        root = workspace.resolve()
        base = safe_path(workspace, args.path)
        if not base.exists():
            raise ToolError(f"no existe la ruta '{args.path or '.'}' en el repositorio")
        if base.is_file():
            return f"{base.relative_to(root).as_posix()} (es un archivo, no un directorio)"

        rows: list[str] = []
        truncated = False
        base_depth = len(base.relative_to(root).parts)

        for entry in sorted(base.rglob("*"), key=lambda p: p.as_posix()):
            if len(rows) >= args.max_entries:
                truncated = True
                break
            if _is_ignored(entry, root):
                continue
            rel = entry.relative_to(root)
            depth = len(rel.parts) - base_depth
            if depth > args.max_depth:
                continue
            if entry.is_dir():
                rows.append(f"{'  ' * (depth - 1)}{rel.name}/")
            else:
                try:
                    size = entry.stat().st_size
                except OSError:
                    size = 0
                rows.append(f"{'  ' * (depth - 1)}{rel.name}  ({_human(size)})")

        if not rows:
            return f"'{args.path or '.'}' no contiene archivos visibles."

        header = f"{base.relative_to(root).as_posix() or '.'}/  ({len(rows)} entradas)"
        body = "\n".join(rows)
        if truncated:
            body += f"\n[...truncado en {args.max_entries} entradas. Usa 'path' para acotar la busqueda.]"
        return f"{header}\n{body}"


# ---------------------------------------------------------------------------
# read_file
# ---------------------------------------------------------------------------


class ReadFileArgs(BaseModel):
    path: str = Field(min_length=1, description="Ruta del archivo relativa a la raiz del repo.")
    offset: int = Field(default=0, ge=0, description="Primera linea a devolver (0-indexed).")
    limit: int = Field(
        default=400, ge=1, le=2000, description="Cantidad maxima de lineas a devolver."
    )


class ReadFileTool:
    name = "read_file"
    description = (
        "Lee un archivo de texto del repositorio y devuelve su contenido numerado por lineas. "
        "Usar despues de list_files, sobre archivos concretos. Para archivos largos usar "
        "offset y limit para leer por partes."
    )
    args_model = ReadFileArgs

    async def run(self, workspace: Path, args: ReadFileArgs) -> str:
        root = workspace.resolve()
        target = safe_path(workspace, args.path)

        if not target.exists():
            siblings = _suggest_siblings(target, root)
            raise ToolError(f"no existe el archivo '{args.path}'.{siblings}")
        if target.is_dir():
            raise ToolError(f"'{args.path}' es un directorio. Usa list_files para ver su contenido.")

        try:
            raw = target.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            raise ToolError(f"'{args.path}' no es un archivo de texto UTF-8 (parece binario).") from None
        except OSError as exc:
            raise ToolError(f"no se pudo leer '{args.path}': {exc}") from None

        lines = raw.splitlines()
        total = len(lines)
        window = lines[args.offset : args.offset + args.limit]
        if not window:
            raise ToolError(
                f"'{args.path}' tiene {total} lineas; offset={args.offset} esta fuera de rango."
            )

        numbered = "\n".join(f"{args.offset + i + 1:>5}  {line}" for i, line in enumerate(window))
        shown_to = args.offset + len(window)
        header = f"{args.path} (lineas {args.offset + 1}-{shown_to} de {total})"
        footer = ""
        if shown_to < total:
            footer = (
                f"\n[...quedan {total - shown_to} lineas. "
                f"Para seguir: read_file(path='{args.path}', offset={shown_to})]"
            )
        return f"{header}\n{numbered}{footer}"


def _suggest_siblings(target: Path, root: Path) -> str:
    """Un error que ademas dice que SI existe cerca vale mucho mas."""
    parent = target.parent
    if not parent.exists() or not parent.is_dir():
        return ""
    try:
        names = sorted(p.name for p in parent.iterdir() if not _is_ignored(p, root))[:15]
    except OSError:
        return ""
    if not names:
        return ""
    where = parent.relative_to(root).as_posix() or "."
    return f" En '{where}/' hay: {', '.join(names)}"


def _human(size: int) -> str:
    if size < 1024:
        return f"{size}B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f}KB"
    return f"{size / (1024 * 1024):.1f}MB"
