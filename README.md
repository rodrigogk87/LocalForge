# LocalForge

Coding agent que corre sobre un **LLM local** (RTX 4090), con un **agent harness propio y explícito**.

Sin LangChain, sin CrewAI, sin frameworks que escondan el loop. Las librerías que se usan son
infraestructura (`httpx`, `pydantic`), no abstracciones de agentes.

> El estado real y actualizado del proyecto vive en **[`PROJECT_STATE.md`](PROJECT_STATE.md)**.
> Si sos un agente retomando este trabajo, empezá por ahí.

## Requisitos

- Ollama corriendo en `localhost:11434`
- Un modelo con soporte de tool calling (default: `qwen3:14b`)
- Python ≥ 3.12 (se gestiona con `uv`)

## Uso

```bash
uv sync --extra dev

# ¿responde el LLM local?
uv run localforge health

# correr el agente sobre un repo
uv run localforge ask <ruta-del-repo> "Explicame este proyecto"
uv run localforge ask . "¿Dónde se valida la entrada del usuario?" -v
```

## Configuración

Todo por variables de entorno (ver `.env.example`):

| Variable | Default | Qué controla |
|---|---|---|
| `LOCALFORGE_PROVIDER` | `ollama` | Backend de inferencia |
| `LOCALFORGE_MODEL` | `qwen3:14b` | Modelo local |
| `LOCALFORGE_NUM_CTX` | `32768` | Ventana de contexto pedida a Ollama |
| `LOCALFORGE_MAX_TURNS` | `20` | Límite de turnos del loop |
| `LOCALFORGE_WALL_CLOCK_S` | `300` | Presupuesto de tiempo por task |
| `LOCALFORGE_TOOL_TIMEOUT_S` | `30` | Timeout por tool |

## Arquitectura

```
usuario → CLI → AgentHarness → ModelProvider → LLM local
                     ↓
                ToolExecutor → tools (filesystem)
                     ↓
                ToolResult → vuelve al contexto → LLM
                     ↓
                AgentOutcome
```

El harness **nunca** conoce a Ollama: habla contra el `Protocol` `ModelProvider`.
Agregar `LlamaCppProvider` u `OpenAIProvider` no toca el loop.

## Tests

```bash
uv run pytest -q
```
