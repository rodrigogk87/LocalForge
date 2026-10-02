"""Configuracion por entorno.

Deliberadamente plana y sin magia: un dataclass que se arma desde env vars,
con defaults neutrales. Cambiar de modelo o de provider no deberia requerir
tocar codigo.

Dos reglas que valen la pena tener presentes:

1. Los defaults del codigo NO son los de ninguna maquina en particular. Lo
   especifico de cada maquina (que modelo hay instalado, cuanto tarda un turno)
   vive en un `.env` que no se commitea. Un default que asume el hardware de
   quien lo escribio es una trampa para el que clona el repo.

2. El entorno se lee al CONSTRUIR (`Settings.from_env()`), no al importar el
   modulo. Si los valores viven en los defaults del dataclass, Python los
   evalua una sola vez al definir la clase: `Settings()` en un test, despues de
   tocar os.environ, devolveria silenciosamente lo que habia al importar.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# Solo se adoptan claves con este prefijo. Ver load_dotenv().
ENV_PREFIX = "LOCALFORGE_"


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


# ---------------------------------------------------------------------------
# .env
# ---------------------------------------------------------------------------


def find_dotenv(start: Path | None = None) -> Path | None:
    """Busca un `.env` desde `start` hacia arriba, hasta la raiz."""
    here = (start or Path.cwd()).resolve()
    for directory in (here, *here.parents):
        candidate = directory / ".env"
        if candidate.is_file():
            return candidate
    return None


def load_dotenv(path: Path | None = None) -> dict[str, str]:
    """Carga variables LOCALFORGE_* de un `.env` al entorno del proceso.

    Tres decisiones que no son obvias:

    - **Solo se adoptan claves con prefijo `LOCALFORGE_`.** Este agente corre
      SOBRE otros repositorios, y esos repositorios tienen su propio `.env` con
      credenciales. Cargarlo entero meteria secretos ajenos en nuestro proceso
      sin ningun motivo. Filtrar por prefijo cuesta una linea.
    - **El shell gana.** Una variable ya exportada no se pisa, asi
      `LOCALFORGE_MODEL=otro localforge ask ...` sigue siendo la ultima palabra.
    - **Sin dependencias.** Son veinte lineas; `python-dotenv` seria la primera
      dependencia del proyecto que no es infraestructura.

    Devuelve lo que efectivamente se aplico, para poder mostrarlo o loguearlo.
    """
    target = path or find_dotenv()
    if target is None:
        return {}

    try:
        raw = target.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        # Un .env ilegible no puede matar al agente: se sigue con el entorno
        # del shell, que es un estado perfectamente valido.
        return {}

    applied: dict[str, str] = {}
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        key, sep, value = line.partition("=")
        if not sep:
            continue
        key = key.strip()
        if not key.startswith(ENV_PREFIX):
            continue
        value = value.strip()
        # Comillas opcionales alrededor del valor, como en cualquier shell.
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if key in os.environ:  # el shell gana
            continue
        os.environ[key] = value
        applied[key] = value
    return applied


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Settings:
    provider: str = "ollama"
    ollama_host: str = "http://localhost:11434"
    model: str = "qwen3:14b"

    # num_ctx de Ollama. Si el prompt lo excede, Ollama TRUNCA en silencio por
    # la izquierda: se come el system prompt sin avisar. Por eso lo fijamos
    # explicitamente en vez de confiar en el default del modelo (2048-4096).
    num_ctx: int = 32768

    # Estos dos son los que mas cambian entre maquinas. Un modelo que pide un
    # archivo por turno gasta el triple de turnos que uno que pide tres en
    # paralelo, y tarda un orden de magnitud mas por turno sin GPU dedicada.
    max_turns: int = 20
    wall_clock_s: float = 300.0

    tool_timeout_s: float = 30.0
    token_budget: int = 200_000

    # Cuanto de un tool result entra al contexto. Sin esto, un read_file de un
    # archivo grande llena la ventana en un solo turno.
    tool_output_limit: int = 8_000

    request_timeout_s: float = 180.0

    state_dir_path: str = ".localforge"

    @classmethod
    def from_env(cls) -> Settings:
        """Arma los settings leyendo os.environ AHORA.

        Cada campo cae a su propio default del dataclass, asi los defaults
        viven en un solo lugar.
        """
        return cls(
            provider=_env(f"{ENV_PREFIX}PROVIDER", cls.provider),
            ollama_host=_env(f"{ENV_PREFIX}OLLAMA_HOST", cls.ollama_host),
            model=_env(f"{ENV_PREFIX}MODEL", cls.model),
            num_ctx=_env_int(f"{ENV_PREFIX}NUM_CTX", cls.num_ctx),
            max_turns=_env_int(f"{ENV_PREFIX}MAX_TURNS", cls.max_turns),
            wall_clock_s=_env_float(f"{ENV_PREFIX}WALL_CLOCK_S", cls.wall_clock_s),
            tool_timeout_s=_env_float(f"{ENV_PREFIX}TOOL_TIMEOUT_S", cls.tool_timeout_s),
            token_budget=_env_int(f"{ENV_PREFIX}TOKEN_BUDGET", cls.token_budget),
            tool_output_limit=_env_int(f"{ENV_PREFIX}TOOL_OUTPUT_LIMIT", cls.tool_output_limit),
            request_timeout_s=_env_float(f"{ENV_PREFIX}REQUEST_TIMEOUT_S", cls.request_timeout_s),
            state_dir_path=_env(f"{ENV_PREFIX}STATE_DIR", cls.state_dir_path),
        )

    @property
    def state_dir(self) -> Path:
        return Path(self.state_dir_path)


# El singleton del proceso: .env primero, entorno despues, defaults al final.
# `Settings()` a secas sigue existiendo y da los defaults del codigo, sin
# entorno -- que es exactamente lo que un test quiere.
DOTENV_APPLIED = load_dotenv()
settings = Settings.from_env()
