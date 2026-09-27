"""search_code: grep lexico sobre el repositorio.

Era la carencia mas grande del proyecto. Con solo `list_files` y `read_file`, un
agente que quiere saber donde se define algo tiene que ADIVINAR que archivo
abrir. Medido en el M1 con gemma4:e4b: ante "¿donde se valida que una ruta no
escape del workspace?", el agente listo el arbol, abrio `cli.py` (mal), lo leyo
dos veces, nunca encontro `safe_path` en `tools/fs.py`, y contesto con "el mas
probable lugar" -- justo la palabra que el system prompt prohibe.

El problema no era el prompt ni el modelo: era que no existia la herramienta
para responder esa pregunta. Buscar es mas barato que leer y muchisimo mas
barato que adivinar.

Ademas es la pieza que desbloquea el retrieval just-in-time de la Fase 2
(W2-C11): traer el fragmento exacto en vez del archivo entero requiere, primero,
poder encontrarlo.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, Field

from localforge.tools.base import ToolError
from localforge.tools.fs import _is_ignored, safe_path

# Un archivo enorme suele ser generado (un lock, un bundle, un dump). Leerlo
# entero para buscar cuesta mucho y casi nunca aporta.
MAX_FILE_BYTES = 2_000_000

# Cuanto de una linea entra en el resultado. Una linea minificada de 40k
# caracteres llenaria la ventana con una sola coincidencia.
MAX_LINE_CHARS = 400


class SearchCodeArgs(BaseModel):
    pattern: str = Field(
        min_length=1,
        description=(
            "Texto a buscar. Por defecto es literal; con regex=true se interpreta como "
            "expresion regular de Python."
        ),
    )
    path: str = Field(
        default="",
        description="Subdirectorio relativo donde buscar. Vacio para todo el repositorio.",
    )
    regex: bool = Field(
        default=False, description="Interpretar 'pattern' como expresion regular."
    )
    case_sensitive: bool = Field(
        default=False, description="Distinguir mayusculas de minusculas."
    )
    glob: str = Field(
        default="",
        description=(
            "Filtro de nombre de archivo, estilo glob. Ej: '*.py' para buscar solo en Python."
        ),
    )
    context_lines: int = Field(
        default=0,
        ge=0,
        le=5,
        description="Lineas de contexto antes y despues de cada coincidencia.",
    )
    max_results: int = Field(
        default=60, ge=1, le=500, description="Cantidad maxima de coincidencias a devolver."
    )


@dataclass
class _Hit:
    path: str
    line_no: int
    line: str
    before: list[tuple[int, str]]
    after: list[tuple[int, str]]


class SearchCodeTool:
    name = "search_code"
    description = (
        "Busca texto en el contenido de los archivos del repositorio y devuelve las "
        "coincidencias con ruta y numero de linea. USAR ANTES de read_file cuando no sepas "
        "en que archivo esta algo: buscar un nombre de funcion, clase, variable o mensaje de "
        "error te lleva directo al lugar, en vez de abrir archivos a ver si estan. Soporta "
        "expresiones regulares (regex=true) y filtro por tipo de archivo (glob='*.py')."
    )
    args_model = SearchCodeArgs

    async def run(self, workspace: Path, args: SearchCodeArgs) -> str:
        root = workspace.resolve()
        base = safe_path(workspace, args.path)

        if not base.exists():
            raise ToolError(f"no existe la ruta '{args.path or '.'}' en el repositorio")

        matcher = _build_matcher(args)
        targets = _candidates(base, root, args.glob)

        hits: list[_Hit] = []
        files_scanned = 0
        truncated = False

        for file in targets:
            if len(hits) >= args.max_results:
                truncated = True
                break
            try:
                if file.stat().st_size > MAX_FILE_BYTES:
                    continue
                text = file.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue  # binario o ilegible: no es un error de la busqueda
            files_scanned += 1

            lines = text.splitlines()
            for i, line in enumerate(lines):
                if len(hits) >= args.max_results:
                    truncated = True
                    break
                if not matcher(line):
                    continue
                hits.append(
                    _Hit(
                        path=file.relative_to(root).as_posix(),
                        line_no=i + 1,
                        line=_clip(line),
                        before=[
                            (j + 1, _clip(lines[j]))
                            for j in range(max(0, i - args.context_lines), i)
                        ],
                        after=[
                            (j + 1, _clip(lines[j]))
                            for j in range(i + 1, min(len(lines), i + 1 + args.context_lines))
                        ],
                    )
                )

        if not hits:
            # Un "no hay resultados" tiene que decir que se busco y donde, o el
            # modelo no puede distinguir "no existe" de "busque mal".
            where = args.path or "todo el repositorio"
            hint = " (con regex)" if args.regex else ""
            extra = f", filtrando por '{args.glob}'" if args.glob else ""
            return (
                f"sin coincidencias para '{args.pattern}'{hint} en {where}{extra}. "
                f"Se revisaron {files_scanned} archivos de texto.\n"
                "Si esperabas encontrarlo: probá un fragmento mas corto, sacá el glob, "
                "o usá regex=true para buscar variantes."
            )

        return _render(hits, args, files_scanned, truncated)


# ---------------------------------------------------------------------------
# internos
# ---------------------------------------------------------------------------


def _build_matcher(args: SearchCodeArgs):
    """Devuelve una funcion linea -> bool.

    Se compila UNA vez fuera del bucle: compilar por linea sobre un repo de
    mil archivos es la diferencia entre instantaneo y varios segundos.
    """
    if args.regex:
        flags = 0 if args.case_sensitive else re.IGNORECASE
        try:
            rx = re.compile(args.pattern, flags)
        except re.error as exc:
            # El modelo escribe la regex: un patron invalido es esperable, no
            # excepcional. El mensaje tiene que decirle como salir del paso.
            raise ToolError(
                f"expresion regular invalida: {exc}. "
                "Si querias buscar el texto tal cual, llama de nuevo sin regex=true."
            ) from None
        return lambda line: rx.search(line) is not None

    if args.case_sensitive:
        needle = args.pattern
        return lambda line: needle in line
    needle = args.pattern.lower()
    return lambda line: needle in line.lower()


def _candidates(base: Path, root: Path, glob: str) -> list[Path]:
    """Archivos de texto candidatos, en orden estable.

    El orden importa para que dos corridas iguales devuelvan lo mismo: si el
    resultado se trunca en 60 coincidencias, un orden inestable haria que el
    modelo vea un subconjunto distinto cada vez.
    """
    if base.is_file():
        return [base] if not _is_ignored(base, root) else []

    out: list[Path] = []
    for entry in sorted(base.rglob("*"), key=lambda p: p.as_posix()):
        if not entry.is_file() or _is_ignored(entry, root):
            continue
        if glob and not entry.match(glob):
            continue
        out.append(entry)
    return out


def _clip(line: str) -> str:
    line = line.rstrip("\n")
    if len(line) <= MAX_LINE_CHARS:
        return line
    return line[:MAX_LINE_CHARS] + f" […+{len(line) - MAX_LINE_CHARS} chars]"


def _render(hits: list[_Hit], args: SearchCodeArgs, files_scanned: int, truncated: bool) -> str:
    by_file: dict[str, list[_Hit]] = {}
    for h in hits:
        by_file.setdefault(h.path, []).append(h)

    parts = [
        f"{len(hits)} coincidencias de '{args.pattern}' en {len(by_file)} archivos "
        f"({files_scanned} archivos de texto revisados)"
    ]
    for path, group in by_file.items():
        parts.append(f"\n{path}")
        for h in group:
            for n, text in h.before:
                parts.append(f"  {n:>5}- {text}")
            # La coincidencia lleva ':' y el contexto '-', igual que grep -C.
            parts.append(f"  {h.line_no:>5}: {h.line}")
            for n, text in h.after:
                parts.append(f"  {n:>5}- {text}")
            if args.context_lines:
                parts.append("")

    if truncated:
        parts.append(
            f"\n[...cortado en {args.max_results} coincidencias. Acotá con path=, glob= "
            "o un patron mas especifico.]"
        )
    parts.append("\nPara ver una coincidencia en su contexto: read_file(path=..., offset=<linea - 20>)")
    return "\n".join(parts)


__all__ = ["SearchCodeTool", "SearchCodeArgs", "MAX_FILE_BYTES", "MAX_LINE_CHARS"]
