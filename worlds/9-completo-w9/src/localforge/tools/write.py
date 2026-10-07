"""write_file: escribir, pero solo donde escribir no cuesta nada.

Los permisos de la Fase 5 ya habilitaban `write_file` (en ASK), pero quedo como
una decision de capacidad sin tomar: escribir sobre el repo del usuario es el
primer efecto irreversible del agente. Se tomo asi: **esta tool solo se registra
adentro de un worktree descartable** (`harness/worktree.py`). Ahi el peor caso es
un directorio temporal que se borra; lo que llega al repo real es un diff que
un humano decide si aplicar.

Escritura atomica (temporal + `os.replace`), igual que los checkpoints: un
archivo a medio escribir es peor que el archivo viejo.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from pydantic import BaseModel, Field

from localforge.tools.base import ToolError
from localforge.tools.fs import safe_path

MAX_BYTES = 200_000


class WriteFileArgs(BaseModel):
    path: str = Field(min_length=1, description="Ruta relativa a la raiz del repo.")
    content: str = Field(description="El contenido COMPLETO del archivo. Reemplaza lo que habia.")


class WriteFileTool:
    name = "write_file"
    description = (
        "Escribe un archivo completo (lo crea o lo reemplaza). Estas trabajando en una COPIA "
        "aislada del repositorio: tus cambios vuelven como un diff para que alguien los revise. "
        "Leé el archivo antes de reemplazarlo, para no perder lo que tenia."
    )
    args_model = WriteFileArgs

    async def run(self, workspace: Path, args: WriteFileArgs) -> str:
        target = safe_path(workspace, args.path)
        if target.is_dir():
            raise ToolError(f"'{args.path}' es un directorio")
        data = args.content.encode("utf-8")
        if len(data) > MAX_BYTES:
            raise ToolError(f"el contenido tiene {len(data)} bytes; el maximo es {MAX_BYTES}")
        if ".git" in Path(args.path).parts:
            raise ToolError("no se escribe adentro de .git")
        target.parent.mkdir(parents=True, exist_ok=True)
        existed = target.exists()
        fd, tmp = tempfile.mkstemp(dir=target.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            os.replace(tmp, target)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        lines = args.content.count("\n") + (0 if args.content.endswith("\n") or not args.content else 1)
        return f"{'reemplazado' if existed else 'creado'} {args.path} ({lines} lineas)"


__all__ = ["WriteFileTool", "WriteFileArgs"]
