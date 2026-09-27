"""La interface entre el harness y cualquier LLM.

Es a proposito angosta: un metodo. Cada metodo que se agrega es un metodo que
TODO implementador tiene que escribir, incluido el adapter de llama.cpp que
todavia no existe. Streaming, caching y batching viven dentro del adapter
concreto, donde el harness no los necesita ver.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from localforge.models import AgentMessage, ModelResponse, ToolDefinition


class ProviderError(RuntimeError):
    """Fallo del proveedor de inferencia.

    `retryable` distingue lo transitorio (conexion caida, 503) de lo
    determinista (modelo inexistente, request mal formada). Reintentar un
    error determinista es quemar tiempo sin chance de exito.
    """

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


@runtime_checkable
class ModelProvider(Protocol):
    """Lo que el harness necesita de un modelo. Nada mas.

    Ojo con `runtime_checkable`: isinstance() solo verifica que los miembros
    EXISTAN por nombre, no sus firmas. Sirve como smoke test al cargar un
    provider, no como validacion real.
    """

    name: str
    model: str

    async def complete(
        self,
        messages: list[AgentMessage],
        tools: list[ToolDefinition] | None = None,
        *,
        system: str | None = None,
        max_tokens: int | None = None,
    ) -> ModelResponse: ...

    async def health(self) -> dict[str, str]:
        """Comprueba que la inferencia esta viva y devuelve datos del backend."""
        ...

    async def aclose(self) -> None: ...
