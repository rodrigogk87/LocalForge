"""Checkpoints: que la corrida sobreviva al proceso.

Desde la Fase 1 el loop tiene un comentario que dice que su estado local "es
exactamente lo que en la Fase 6 se serializa en un checkpoint". Este modulo es
el cobro de esa promesa, y la promesa se pudo cobrar por una sola razon: el
estado estaba **nombrado**. `messages`, `tokens_in/out`, `seen`, `trajectory`,
`records`. Lo que es facil de nombrar es facil de persistir.

Tres propiedades que separan un checkpoint de un `json.dump`:

1. **Escritura atomica.** Se escribe a un temporal y se renombra. `os.replace`
   es atomico en POSIX y en Windows, asi que un crash a mitad de escritura deja
   el checkpoint ANTERIOR intacto en vez de un archivo truncado. Un checkpoint
   corrupto es peor que no tener checkpoint: te hace creer que podes resumir.

2. **Version de esquema.** El checkpoint que escribe el proceso de hoy lo va a
   leer el proceso de mañana, con el codigo cambiado. Sin version, un campo
   renombrado es un crash silencioso en el peor momento posible.

3. **Idempotencia al resumir.** Resumir no puede repetir efectos. Hoy es gratis
   porque todas las tools son de lectura, pero la propiedad hay que diseñarla
   ahora: el checkpoint se graba DESPUES de aplicar los resultados al estado, asi
   que al resumir nunca se re-ejecuta una tool cuyo resultado ya esta guardado.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Protocol, runtime_checkable
from uuid import UUID

from pydantic import BaseModel, Field

from localforge.models import AgentMessage, AgentStatus, AgentTask, TurnRecord

# Subir esto cuando cambie la forma del checkpoint de manera incompatible.
CHECKPOINT_VERSION = 1


class IncompatibleCheckpoint(RuntimeError):
    """El checkpoint fue escrito por una version que no sabemos leer."""


class Checkpoint(BaseModel):
    """Una foto del loop al final de un turno.

    Es un modelo Pydantic y no un dataclass porque tiene que cruzar un borde de
    serializacion, que es justamente el criterio de la Fase 1 para usar Pydantic:
    todo lo que cruza un borde de confianza se valida. Un checkpoint escrito por
    otra version del codigo es input no confiable.
    """

    version: int = CHECKPOINT_VERSION
    task: AgentTask
    turn: int = Field(ge=0, description="Turnos ya completados")
    status: AgentStatus = AgentStatus.RUNNING

    messages: list[AgentMessage] = Field(default_factory=list)
    tokens_in: int = 0
    tokens_out: int = 0
    # El Counter de deteccion de loops. Si no se persiste, al resumir el agente
    # se olvida de que ya repitio dos veces la misma llamada y el limite de
    # repeticiones se reinicia: un agente en loop podria girar para siempre
    # cruzando reinicios.
    seen: dict[str, int] = Field(default_factory=dict)
    trajectory: list[str] = Field(default_factory=list)
    records: list[TurnRecord] = Field(default_factory=list)

    state_path: str = ""
    repairs: int = 0
    rejected_by: list[str] = Field(default_factory=list)
    # Calibracion del estimador de tokens. Es barata de perder pero gratis de
    # guardar, y al resumir evita volver a arrancar en la heuristica cruda.
    chars_per_token: float | None = None

    @property
    def task_id(self) -> UUID:
        return self.task.id


# ---------------------------------------------------------------------------
# Stores
# ---------------------------------------------------------------------------


@runtime_checkable
class CheckpointStore(Protocol):
    """Donde viven los checkpoints. Angosto: guardar, cargar, listar."""

    def save(self, checkpoint: Checkpoint) -> None: ...

    def load(self, task_id: UUID | str) -> Checkpoint | None: ...

    def list_ids(self) -> list[str]: ...


class MemoryCheckpointStore:
    """Para tests. No sobrevive al proceso, y ese es el punto de los tests."""

    def __init__(self) -> None:
        self.saved: dict[str, Checkpoint] = {}
        self.writes = 0

    def save(self, checkpoint: Checkpoint) -> None:
        self.saved[str(checkpoint.task_id)] = checkpoint
        self.writes += 1

    def load(self, task_id: UUID | str) -> Checkpoint | None:
        return self.saved.get(str(task_id))

    def list_ids(self) -> list[str]:
        return sorted(self.saved)


class FileCheckpointStore:
    """Un JSON por task, en `.localforge/runs/`.

    Un archivo por task y no un append-log: al resumir sólo interesa el ULTIMO
    estado, y un log obligaria a reproducir la historia entera para reconstruirlo.
    La historia ya vive adentro del checkpoint (`records`, `state_path`).
    """

    def __init__(self, root: Path | str = ".localforge") -> None:
        self.root = Path(root) / "runs"

    def _path(self, task_id: UUID | str) -> Path:
        return self.root / f"{task_id}.json"

    def save(self, checkpoint: Checkpoint) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        payload = checkpoint.model_dump_json(indent=2)
        target = self._path(checkpoint.task_id)

        # Temporal en el MISMO directorio: os.replace solo es atomico dentro del
        # mismo filesystem, y /tmp puede ser otro.
        fd, tmp = tempfile.mkstemp(dir=self.root, prefix=".tmp-", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(payload)
                fh.flush()
                # fsync antes del rename: sin esto el rename puede llegar al
                # disco antes que el contenido, y un corte de luz deja un
                # archivo valido pero vacio.
                os.fsync(fh.fileno())
            os.replace(tmp, target)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def load(self, task_id: UUID | str) -> Checkpoint | None:
        path = self._path(task_id)
        if not path.is_file():
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise IncompatibleCheckpoint(
                f"el checkpoint '{path}' no se puede leer: {exc}"
            ) from exc

        version = raw.get("version")
        if version != CHECKPOINT_VERSION:
            # Explicito y temprano. Un campo que cambio de forma produciria un
            # error de validacion confuso mucho mas adelante.
            raise IncompatibleCheckpoint(
                f"el checkpoint '{path}' es version {version} y este codigo lee "
                f"version {CHECKPOINT_VERSION}. Borralo o migralo a mano."
            )
        return Checkpoint.model_validate(raw)

    def list_ids(self) -> list[str]:
        if not self.root.is_dir():
            return []
        return sorted(p.stem for p in self.root.glob("*.json"))


__all__ = [
    "Checkpoint",
    "CheckpointStore",
    "MemoryCheckpointStore",
    "FileCheckpointStore",
    "IncompatibleCheckpoint",
    "CHECKPOINT_VERSION",
]
