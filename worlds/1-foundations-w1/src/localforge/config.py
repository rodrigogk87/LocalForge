"""Configuracion por entorno.

Deliberadamente plana y sin magia: un dataclass que se arma desde env vars,
con defaults que funcionan en esta maquina. Cambiar de modelo o de provider
no deberia requerir tocar codigo.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


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


@dataclass(frozen=True)
class Settings:
    provider: str = _env("LOCALFORGE_PROVIDER", "ollama")
    ollama_host: str = _env("LOCALFORGE_OLLAMA_HOST", "http://localhost:11434")
    model: str = _env("LOCALFORGE_MODEL", "qwen3:14b")

    # num_ctx de Ollama. Si el prompt lo excede, Ollama TRUNCA en silencio por
    # la izquierda: se come el system prompt sin avisar. Por eso lo fijamos
    # explicitamente en vez de confiar en el default del modelo (2048-4096).
    num_ctx: int = _env_int("LOCALFORGE_NUM_CTX", 32768)

    max_turns: int = _env_int("LOCALFORGE_MAX_TURNS", 20)
    wall_clock_s: float = _env_float("LOCALFORGE_WALL_CLOCK_S", 300.0)
    tool_timeout_s: float = _env_float("LOCALFORGE_TOOL_TIMEOUT_S", 30.0)
    token_budget: int = _env_int("LOCALFORGE_TOKEN_BUDGET", 200_000)

    # Cuanto de un tool result entra al contexto. Sin esto, un read_file de un
    # archivo grande llena la ventana en un solo turno.
    tool_output_limit: int = _env_int("LOCALFORGE_TOOL_OUTPUT_LIMIT", 8_000)

    request_timeout_s: float = _env_float("LOCALFORGE_REQUEST_TIMEOUT_S", 180.0)

    @property
    def state_dir(self) -> Path:
        return Path(_env("LOCALFORGE_STATE_DIR", ".localforge"))


settings = Settings()
