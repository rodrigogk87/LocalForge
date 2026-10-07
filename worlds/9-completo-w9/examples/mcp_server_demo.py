"""Un server MCP minimo, por stdio, sin dependencias.

Existe para dos cosas: que los tests del cliente hablen con un server REAL (un
proceso aparte, por un pipe, con el handshake completo) y que puedas probar
`--mcp` sin instalar nada:

    uv run lfw9 ask . "cuanto es 2+40? usa la tool" --mcp examples/mcp.json

Publica tres tools: `add` (suma), `word_count` (cuenta palabras de un texto) y
`fail` (siempre devuelve isError, para ver como llega un error remoto).
"""

import json
import sys

TOOLS = [
    {
        "name": "add",
        "description": "Suma dos numeros.",
        "inputSchema": {
            "type": "object",
            "properties": {"a": {"type": "number"}, "b": {"type": "number"}},
            "required": ["a", "b"],
        },
    },
    {
        "name": "word_count",
        "description": "Cuenta las palabras de un texto.",
        "inputSchema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    },
    {
        "name": "fail",
        "description": "Siempre falla. Sirve para ver como llega un error de una tool remota.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def handle(msg):
    method, params = msg.get("method"), msg.get("params") or {}
    if method == "initialize":
        return {
            "protocolVersion": params.get("protocolVersion", "2025-06-18"),
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "demo", "version": "1.0"},
        }
    if method == "tools/list":
        return {"tools": TOOLS}
    if method == "tools/call":
        name, args = params.get("name"), params.get("arguments") or {}
        if name == "add":
            try:
                total = float(args["a"]) + float(args["b"])
            except (KeyError, TypeError, ValueError):
                return {"content": [{"type": "text", "text": "add necesita a y b numericos"}], "isError": True}
            return {"content": [{"type": "text", "text": f"{total:g}"}]}
        if name == "word_count":
            return {"content": [{"type": "text", "text": str(len(str(args.get("text", "")).split()))}]}
        if name == "fail":
            return {"content": [{"type": "text", "text": "fallo a proposito"}], "isError": True}
        raise LookupError(f"tool desconocida: {name}")
    raise LookupError(f"metodo desconocido: {method}")


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        msg = json.loads(line)
        if "id" not in msg:
            continue  # notificacion (p. ej. notifications/initialized)
        try:
            reply = {"jsonrpc": "2.0", "id": msg["id"], "result": handle(msg)}
        except LookupError as exc:
            reply = {"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -32601, "message": str(exc)}}
        sys.stdout.write(json.dumps(reply) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
