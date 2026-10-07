"""La tool `retrieve`: expone el indice de `localforge/retrieval.py` al modelo.

Ver ese modulo para el por que (BM25, troceo por funcion, secretos fuera del
indice). Aca solo esta la interfaz con el modelo.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from localforge.retrieval import Index
from localforge.tools.base import ToolError
from localforge.tools.fs import safe_path


class RetrieveArgs(BaseModel):
    query: str = Field(
        min_length=2,
        description=(
            "Lo que buscas, en lenguaje natural o con identificadores. Ej: "
            "'donde se valida que una ruta no salga del workspace'."
        ),
    )
    k: int = Field(default=4, ge=1, le=8, description="Cuantos fragmentos traer.")


class RetrieveTool:
    name = "retrieve"
    description = (
        "Trae los fragmentos de codigo MAS RELEVANTES para una pregunta, de cualquier "
        "archivo, enteros y con numeros de linea. Usalo cuando no sabes en que archivo "
        "esta algo o no sabes el nombre exacto: es mas barato que leer archivos enteros. "
        "Si ya sabes el identificador exacto, search_code es mas preciso."
    )
    args_model = RetrieveArgs

    def __init__(self) -> None:
        # Cache por workspace. El indice se arma la primera vez que se usa: un
        # agente que nunca llama a retrieve no paga el costo de indexar.
        self._cache: dict[Path, Index] = {}

    def index_for(self, workspace: Path) -> Index:
        root = safe_path(workspace, "")
        if root not in self._cache:
            self._cache[root] = Index.build(root)
        return self._cache[root]

    async def run(self, workspace: Path, args: RetrieveArgs) -> str:
        idx = self.index_for(workspace)
        hits = idx.search(args.query, args.k)
        if not hits:
            raise ToolError(
                f"ningun fragmento coincide con '{args.query}'. Proba con otras palabras, "
                "con el nombre de una funcion, o con list_files para orientarte."
            )
        blocks = []
        for score, ch in hits:
            numbered = "\n".join(
                f"{ch.start + i:>5}  {line}" for i, line in enumerate(ch.text.splitlines())
            )
            blocks.append(f"--- {ch.ref}  (relevancia {score:.1f})\n{numbered}")
        head = f"{len(hits)} fragmento(s) para '{args.query}' (de {len(idx.chunks)} indexados):"
        return head + "\n\n" + "\n\n".join(blocks)



__all__ = ["RetrieveArgs", "RetrieveTool"]
