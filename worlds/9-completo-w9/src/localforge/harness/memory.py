"""Memoria entre sesiones (W6): lo que el agente aprendio ayer, hoy.

El checkpoint es memoria *de una corrida*: sirve para retomarla, y muere con
ella. Esto es lo otro: que la corrida de hoy sepa que ayer alguien ya pregunto
"¿donde se valida la ruta?" y la respuesta verificada fue `tools/fs.py:28`.

Es **memoria episodica**: se guardan corridas (pregunta, respuesta, archivos
leidos), no "hechos" sueltos. Tres reglas, y las tres son de seguridad mas que
de utilidad:

1. **Solo se recuerda lo que paso el verifier.** Una respuesta rechazada en
   memoria es un error que se repite solo, corrida tras corrida, con la
   autoridad de "ya lo sabia". El harness llama a `remember()` unicamente al
   cerrar en COMPLETED.

2. **La memoria NO es evidencia.** Entra al system prompt marcada como
   "puede estar desactualizada, verificá leyendo". Y no hay forma de que cuente
   como evidencia aunque el modelo quiera: el verifier mira la trayectoria de
   tools de ESTA corrida, y recordar no es una tool. El codigo cambio desde
   ayer; lo que se recuerda es por donde empezar a buscar, no la respuesta.

3. **Por repositorio, y fuera del repositorio.** Se guarda en el `state_dir` de
   LocalForge, indexado por la ruta del repo. Nunca adentro del repo analizado:
   escribir ahi seria un efecto sobre codigo ajeno, y un repo hostil podria
   traer una "memoria" plantada.

Cual recordar se decide con el mismo BM25 que el retrieval. Se recuerdan pocas
(3) y cortas: la memoria compite por el mismo contexto que todo lo demas.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field

from localforge.harness.context import MEMORY_MARKER
from localforge.models import AgentOutcome, AgentTask
from localforge.retrieval import bm25, tokenize

RECALL_K = 3
# Cuanto de cada respuesta entra al contexto. La memoria es un puntero ("ya se
# vio en tools/fs.py:28"), no un reemplazo de leer.
ANSWER_CHARS = 500
# Techo de recuerdos por repo. Sin techo, el archivo crece para siempre y el
# BM25 se recalcula sobre miles de entradas en cada corrida.
MAX_ENTRIES = 200


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class MemoryEntry(BaseModel):
    task_id: str
    objective: str
    answer: str
    files: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_utcnow)


@runtime_checkable
class MemoryStore(Protocol):
    def recall(self, workspace: Path, query: str, k: int = RECALL_K) -> list[MemoryEntry]: ...

    def remember(self, workspace: Path, task: AgentTask, outcome: AgentOutcome) -> None: ...


def files_read(outcome: AgentOutcome) -> list[str]:
    """Los archivos que la corrida efectivamente leyo, en orden y sin repetir."""
    out: list[str] = []
    for rec in outcome.turn_records:
        for call in rec.tool_calls:
            path = call.arguments.get("path")
            if call.name == "read_file" and path and path not in out:
                out.append(str(path))
    return out


def entry_from(task: AgentTask, outcome: AgentOutcome) -> MemoryEntry:
    return MemoryEntry(
        task_id=str(task.id),
        objective=task.objective,
        answer=outcome.output[:ANSWER_CHARS],
        files=files_read(outcome),
    )


def rank(entries: list[MemoryEntry], query: str, k: int) -> list[MemoryEntry]:
    docs = [Counter(tokenize(e.objective + " " + e.answer + " " + " ".join(e.files))) for e in entries]
    scores = bm25(docs, tokenize(query))
    ranked = sorted(
        (pair for pair in zip(scores, entries) if pair[0] > 0),
        key=lambda pair: (-pair[0], -pair[1].created_at.timestamp()),
    )
    return [e for _, e in ranked[:k]]


class InMemoryStore:
    """Para tests: misma interfaz, nada en disco."""

    def __init__(self) -> None:
        self.by_repo: dict[str, list[MemoryEntry]] = {}

    def recall(self, workspace: Path, query: str, k: int = RECALL_K) -> list[MemoryEntry]:
        return rank(self.by_repo.get(str(workspace.resolve()), []), query, k)

    def remember(self, workspace: Path, task: AgentTask, outcome: AgentOutcome) -> None:
        bucket = self.by_repo.setdefault(str(workspace.resolve()), [])
        bucket.append(entry_from(task, outcome))
        del bucket[:-MAX_ENTRIES]


class FileMemoryStore:
    """Un JSONL por repositorio en `<state_dir>/memory/`.

    El nombre del archivo es un hash de la ruta absoluta del repo, mas su nombre
    para que sea legible. Dos clones del mismo proyecto en rutas distintas son
    dos memorias distintas: pueden estar en commits distintos.
    """

    def __init__(self, root: Path | str = ".localforge") -> None:
        self.dir = Path(root) / "memory"

    def path_for(self, workspace: Path) -> Path:
        resolved = workspace.resolve()
        digest = hashlib.sha256(str(resolved).encode()).hexdigest()[:12]
        return self.dir / f"{resolved.name}-{digest}.jsonl"

    def load(self, workspace: Path) -> list[MemoryEntry]:
        path = self.path_for(workspace)
        if not path.is_file():
            return []
        out: list[MemoryEntry] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                out.append(MemoryEntry.model_validate_json(line))
            except ValueError:
                # Una linea corrupta no borra la memoria entera: se saltea.
                continue
        return out

    def recall(self, workspace: Path, query: str, k: int = RECALL_K) -> list[MemoryEntry]:
        return rank(self.load(workspace), query, k)

    def remember(self, workspace: Path, task: AgentTask, outcome: AgentOutcome) -> None:
        entries = (self.load(workspace) + [entry_from(task, outcome)])[-MAX_ENTRIES:]
        path = self.path_for(workspace)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Escritura atomica, como los checkpoints: un crash a mitad de escritura
        # deja la memoria anterior entera, no un archivo truncado.
        fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                for e in entries:
                    fh.write(json.dumps(e.model_dump(mode="json"), ensure_ascii=False) + "\n")
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise


def render_block(entries: list[MemoryEntry]) -> str:
    if not entries:
        return ""
    lines = [
        f"{MEMORY_MARKER} sobre este repositorio. Son respuestas que ya pasaron la "
        "verificacion, pero el codigo pudo cambiar desde entonces: NO son evidencia. "
        "Usalas para saber por donde empezar y volvé a leer antes de afirmar."
    ]
    for e in entries:
        when = e.created_at.strftime("%Y-%m-%d")
        files = f" (leyo: {', '.join(e.files[:5])})" if e.files else ""
        answer = " ".join(e.answer.split())
        lines.append(f"- [{when}] P: {e.objective}\n  R: {answer}{files}")
    return "\n".join(lines)


__all__ = [
    "MemoryEntry", "MemoryStore", "InMemoryStore", "FileMemoryStore",
    "render_block", "files_read", "RECALL_K",
]
