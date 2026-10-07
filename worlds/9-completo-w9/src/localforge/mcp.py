"""Cliente MCP (W4-C25..C27): tools que no estan en este repositorio.

Las skills (Fase 4) extienden lo que el agente SABE sin recompilar. MCP extiende
lo que PUEDE HACER: un server MCP es un proceso aparte que publica tools, y el
agente las usa como si fueran propias.

El protocolo, en lo que hace falta para usarlo:

    transporte  stdio: el cliente lanza el server y le habla por stdin/stdout,
                un mensaje JSON-RPC 2.0 por linea
    handshake   initialize -> (respuesta) -> notifications/initialized
    descubrir   tools/list  -> [{name, description, inputSchema}]
    usar        tools/call  -> {content: [{type: "text", text}], isError}

Sin SDK, a proposito: el subconjunto que usa un cliente son ~150 lineas, y
escribirlas es la mejor forma de ver que MCP no tiene nada magico -- es JSON por
un pipe. Lo que si importa esta en los bordes:

1. **Una tool MCP es una tool mas para el harness.** Pasa por el mismo
   ToolExecutor: se valida, se autoriza, se trunca. Y como la politica no la
   conoce, **cae en ASK** -- la regla de la Fase 5 ("una tool nueva es ASK por
   construccion") funcionando sin una linea nueva de permisos.

2. **Nombres con prefijo** `mcp__<server>__<tool>`. Dos servers pueden publicar
   un `search`; sin prefijo, uno pisaria al otro, y peor, podria pisar a una
   tool nuestra.

3. **La config de servers NO se lee del repo analizado.** Un `mcp.json` en el
   repo seria una lista de comandos que el repo te hace ejecutar al abrirlo:
   un repo hostil tendria ejecucion de codigo gratis. La config se pasa
   explicita (`--mcp archivo.json`) y vive donde la pusiste vos.

4. **`isError` es feedback, no excepcion.** Un error de la tool remota vuelve
   al modelo como cualquier ToolError. Un server que se cae, tambien.
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from localforge.tools.base import ToolError, ToolRegistry

PROTOCOL_VERSION = "2025-06-18"
CLIENT_INFO = {"name": "localforge", "version": "9.0.0"}

# Cuanto se espera una respuesta del server. Un server colgado no puede colgar
# al agente: el executor tiene su propio timeout, pero el handshake no pasa por el.
REQUEST_TIMEOUT_S = 20.0


class MCPError(RuntimeError):
    pass


@dataclass(frozen=True)
class ServerConfig:
    name: str
    command: str
    args: tuple[str, ...] = ()
    env: dict[str, str] = field(default_factory=dict)


def load_config(path: Path | str) -> list[ServerConfig]:
    """Formato compatible con el de Claude Code / Claude Desktop:

        {"mcpServers": {"nombre": {"command": "...", "args": [...], "env": {...}}}}
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    servers = data.get("mcpServers", {})
    if not isinstance(servers, dict):
        raise MCPError("'mcpServers' tiene que ser un objeto {nombre: {command, args}}")
    out = []
    for name, spec in servers.items():
        if not isinstance(spec, dict) or not spec.get("command"):
            raise MCPError(f"el server '{name}' no tiene 'command'")
        out.append(
            ServerConfig(
                name=name,
                command=str(spec["command"]),
                args=tuple(str(a) for a in spec.get("args", [])),
                env={str(k): str(v) for k, v in spec.get("env", {}).items()},
            )
        )
    return out


class MCPClient:
    """Una conexion a UN server por stdio."""

    def __init__(self, config: ServerConfig) -> None:
        self.config = config
        self.proc: asyncio.subprocess.Process | None = None
        self._next_id = 0
        self._lock = asyncio.Lock()
        self.server_info: dict[str, Any] = {}

    async def start(self) -> None:
        env = {**os.environ, **self.config.env}
        try:
            self.proc = await asyncio.create_subprocess_exec(
                self.config.command,
                *self.config.args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                env=env,
                # Lineas largas: un tools/list con schemas grandes supera el
                # limite de 64KB por defecto de StreamReader.
                limit=4 * 1024 * 1024,
            )
        except (FileNotFoundError, PermissionError) as exc:
            raise MCPError(f"no se pudo lanzar '{self.config.command}': {exc}") from None
        result = await self.request(
            "initialize",
            {"protocolVersion": PROTOCOL_VERSION, "capabilities": {}, "clientInfo": CLIENT_INFO},
        )
        self.server_info = result.get("serverInfo", {})
        await self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    async def _send(self, message: dict[str, Any]) -> None:
        if self.proc is None or self.proc.stdin is None:
            raise MCPError("el server no esta corriendo")
        self.proc.stdin.write((json.dumps(message) + "\n").encode())
        await self.proc.stdin.drain()

    async def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        # Un request a la vez por server: la correlacion por id existe en el
        # protocolo, pero serializar es mas simple y un server stdio es secuencial.
        async with self._lock:
            self._next_id += 1
            rid = self._next_id
            await self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}})
            assert self.proc is not None and self.proc.stdout is not None
            while True:
                try:
                    line = await asyncio.wait_for(self.proc.stdout.readline(), timeout=REQUEST_TIMEOUT_S)
                except asyncio.TimeoutError:
                    raise MCPError(f"'{self.config.name}' no respondio a {method} en {REQUEST_TIMEOUT_S:.0f}s") from None
                if not line:
                    raise MCPError(f"el server '{self.config.name}' se cerro")
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue  # un server que loguea a stdout: se ignora la linea
                # Notificaciones o requests del server (sin nuestro id): no los
                # usamos, se descartan.
                if msg.get("id") != rid:
                    continue
                if "error" in msg:
                    err = msg["error"]
                    raise MCPError(f"{method}: {err.get('message', err)}")
                return msg.get("result", {})

    async def list_tools(self) -> list[dict[str, Any]]:
        return list((await self.request("tools/list")).get("tools", []))

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> tuple[bool, str]:
        result = await self.request("tools/call", {"name": name, "arguments": arguments})
        texts = []
        for item in result.get("content", []):
            if item.get("type") == "text":
                texts.append(item.get("text", ""))
            else:
                texts.append(f"[contenido {item.get('type', '?')} omitido]")
        return not result.get("isError", False), "\n".join(texts) or "(sin salida)"

    async def close(self) -> None:
        if self.proc is None:
            return
        if self.proc.stdin is not None:
            self.proc.stdin.close()
        try:
            await asyncio.wait_for(self.proc.wait(), timeout=2)
        except asyncio.TimeoutError:
            self.proc.kill()
            await self.proc.wait()
        self.proc = None


# ---------------------------------------------------------------------------
# Adaptador: una tool MCP como una Tool del harness
# ---------------------------------------------------------------------------


def _args_model(tool_name: str, schema: dict[str, Any]) -> type[BaseModel]:
    """Un modelo de argumentos que acepta lo que el server pidio.

    La validacion fina la hace el server (es el dueño del schema). Aca se acepta
    cualquier objeto, y `model_json_schema()` devuelve el schema del server tal
    cual, que es lo que el modelo tiene que ver.
    """
    published = dict(schema or {"type": "object", "properties": {}})

    class _Args(BaseModel):
        model_config = ConfigDict(extra="allow")

        @classmethod
        def model_json_schema(cls, *args: Any, **kwargs: Any) -> dict[str, Any]:  # type: ignore[override]
            return dict(published)

    _Args.__name__ = f"MCPArgs_{tool_name}"
    return _Args


class MCPTool:
    def __init__(self, client: MCPClient, spec: dict[str, Any]) -> None:
        self.client = client
        self.remote_name = str(spec["name"])
        self.name = f"mcp__{client.config.name}__{self.remote_name}"
        self.description = (
            f"[MCP · {client.config.name}] " + (spec.get("description") or self.remote_name)
        )
        self.args_model = _args_model(self.remote_name, spec.get("inputSchema", {}))

    async def run(self, workspace: Path, args: BaseModel) -> str:
        try:
            ok, text = await self.client.call_tool(self.remote_name, args.model_dump())
        except MCPError as exc:
            raise ToolError(f"el server MCP '{self.client.config.name}' fallo: {exc}") from None
        if not ok:
            raise ToolError(text)
        return text


class MCPSession:
    """Arranca los servers, registra sus tools, y los cierra al final.

    Se usa como context manager async para que ningun server quede huerfano si
    la corrida termina por excepcion.
    """

    def __init__(self, configs: list[ServerConfig]) -> None:
        self.configs = configs
        self.clients: list[MCPClient] = []
        self.tools: list[MCPTool] = []
        self.errors: list[str] = []

    async def __aenter__(self) -> MCPSession:
        for cfg in self.configs:
            client = MCPClient(cfg)
            try:
                await client.start()
                specs = await client.list_tools()
            except MCPError as exc:
                # Un server roto no impide arrancar: el agente sigue sin sus tools.
                self.errors.append(f"{cfg.name}: {exc}")
                await client.close()
                continue
            self.clients.append(client)
            self.tools.extend(MCPTool(client, s) for s in specs if s.get("name"))
        return self

    async def __aexit__(self, *exc: object) -> None:
        for client in self.clients:
            await client.close()

    def register_into(self, registry: ToolRegistry) -> list[str]:
        for tool in self.tools:
            registry.register(tool)
        return [t.name for t in self.tools]


__all__ = ["MCPClient", "MCPSession", "MCPTool", "MCPError", "ServerConfig", "load_config"]
