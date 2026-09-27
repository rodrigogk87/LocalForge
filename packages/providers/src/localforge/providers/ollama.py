"""Provider de Ollama: el unico modulo de LocalForge que conoce su API.

Dos cosas de Ollama que el harness NO debe enterarse nunca:

1. `/api/chat` devuelve tool_calls SIN id. La API de OpenAI/Anthropic si los
   trae. Como el harness correlaciona resultados por call_id, el provider
   sintetiza ids estables aca. Es exactamente el borde donde corresponde:
   el harness pide una garantia y el adapter la cumple como pueda.

2. `num_ctx` por defecto es chico y, si el prompt lo excede, Ollama trunca en
   silencio por la izquierda -- se come el system prompt sin avisar. Lo
   fijamos explicito en cada request.
"""

from __future__ import annotations

import time
from typing import Any
from uuid import uuid4

import httpx

from localforge.config import Settings, settings as default_settings
from localforge.models import (
    AgentMessage,
    ModelResponse,
    StopReason,
    ToolCall,
    ToolDefinition,
)
from localforge.providers.base import ProviderError

# done_reason de Ollama -> nuestra senal de control.
_STOP_MAP = {
    "stop": StopReason.END_TURN,
    "length": StopReason.MAX_TOKENS,
    "load": StopReason.UNKNOWN,
}


class OllamaProvider:
    name = "ollama"

    def __init__(self, cfg: Settings | None = None, *, client: httpx.AsyncClient | None = None) -> None:
        self.cfg = cfg or default_settings
        self.model = self.cfg.model
        self._host = self.cfg.ollama_host.rstrip("/")
        self._client = client or httpx.AsyncClient(timeout=self.cfg.request_timeout_s)
        self._owns_client = client is None
        # Se apaga solo si el modelo no soporta thinking (ver _chat).
        self._supports_think: bool | None = None
        self._call_seq = 0

    # -- infra --------------------------------------------------------------

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def health(self) -> dict[str, str]:
        try:
            version = (await self._client.get(f"{self._host}/api/version", timeout=10)).json()
            tags = (await self._client.get(f"{self._host}/api/tags", timeout=10)).json()
        except httpx.HTTPError as exc:
            raise ProviderError(f"no se pudo contactar Ollama en {self._host}: {exc}", retryable=True) from exc

        installed = [m.get("model", "") for m in tags.get("models", [])]
        if self.model not in installed:
            raise ProviderError(
                f"el modelo '{self.model}' no esta instalado. Disponibles: {', '.join(installed) or 'ninguno'}. "
                f"Instalalo con: ollama pull {self.model}"
            )
        return {
            "provider": self.name,
            "host": self._host,
            "version": str(version.get("version", "?")),
            "model": self.model,
            "installed_models": ", ".join(installed),
        }

    # -- inferencia ---------------------------------------------------------

    def _next_call_id(self) -> str:
        """Ids sinteticos, unicos dentro del proceso.

        El contador da legibilidad en los logs; el sufijo aleatorio evita
        colisiones si dos harnesses comparten provider.
        """
        self._call_seq += 1
        return f"call_{self._call_seq:03d}_{uuid4().hex[:6]}"

    def _payload(
        self,
        messages: list[AgentMessage],
        tools: list[ToolDefinition] | None,
        system: str | None,
        max_tokens: int | None,
    ) -> dict[str, Any]:
        wire: list[dict[str, Any]] = []
        if system:
            wire.append({"role": "system", "content": system})
        wire.extend(m.to_ollama() for m in messages)

        options: dict[str, Any] = {"num_ctx": self.cfg.num_ctx}
        if max_tokens is not None:
            options["num_predict"] = max_tokens

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": wire,
            "stream": False,
            "options": options,
        }
        if tools:
            payload["tools"] = [t.to_ollama() for t in tools]
        return payload

    async def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            response = await self._client.post(f"{self._host}/api/chat", json=payload)
        except httpx.HTTPError as exc:
            raise ProviderError(f"fallo de red contra Ollama: {exc}", retryable=True) from exc

        if response.status_code >= 400:
            body = response.text[:500]
            # 4xx es determinista: la misma request va a fallar igual.
            raise ProviderError(
                f"Ollama respondio {response.status_code}: {body}",
                retryable=response.status_code >= 500,
            )
        return response.json()

    async def complete(
        self,
        messages: list[AgentMessage],
        tools: list[ToolDefinition] | None = None,
        *,
        system: str | None = None,
        max_tokens: int | None = None,
    ) -> ModelResponse:
        payload = self._payload(messages, tools, system, max_tokens)

        # qwen3 y otros modelos razonadores piensan por defecto: mucha latencia
        # por turno y el razonamiento va a `message.thinking`, no a `content`.
        # Lo apagamos, y si el modelo no lo soporta reintentamos sin el campo.
        if self._supports_think is not False:
            payload["think"] = False

        started = time.monotonic()
        try:
            data = await self._post(payload)
            self._supports_think = True
        except ProviderError as exc:
            if self._supports_think is None and "think" in str(exc).lower():
                self._supports_think = False
                payload.pop("think", None)
                data = await self._post(payload)
            else:
                raise

        elapsed_ms = int((time.monotonic() - started) * 1000)
        message = data.get("message") or {}

        tool_calls: list[ToolCall] = []
        for raw in message.get("tool_calls") or []:
            fn = raw.get("function") or {}
            name = fn.get("name")
            if not name:
                continue  # una tool call sin nombre no es accionable
            args = fn.get("arguments")
            if not isinstance(args, dict):
                # Algunos modelos devuelven los argumentos como string JSON.
                args = _coerce_args(args)
            tool_calls.append(ToolCall(id=self._next_call_id(), name=name, arguments=args))

        done_reason = str(data.get("done_reason") or "")
        stop = _STOP_MAP.get(done_reason, StopReason.UNKNOWN)
        if tool_calls:
            # Ollama manda done_reason="stop" aunque haya pedido tools; para el
            # loop lo que importa es que hay trabajo pendiente.
            stop = StopReason.TOOL_USE

        content = message.get("content") or None
        return ModelResponse(
            content=content,
            tool_calls=tool_calls,
            stop_reason=stop,
            input_tokens=int(data.get("prompt_eval_count") or 0),
            output_tokens=int(data.get("eval_count") or 0),
            model=str(data.get("model") or self.model),
            duration_ms=elapsed_ms,
        )


def _coerce_args(raw: Any) -> dict[str, Any]:
    """Los argumentos deberian ser un objeto; algunos modelos mandan string."""
    import json

    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}
