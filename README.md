# LocalForge

Coding agent que corre sobre un **LLM local** (RTX 4090), con un **agent harness propio y explícito**.

Sin LangChain, sin CrewAI, sin frameworks que escondan el loop. Las librerías que se usan son
infraestructura (`httpx`, `pydantic`), no abstracciones de agentes.

## Por dónde empezar

| Si sos… | Leé |
|---|---|
| 🧑‍🎓 **una persona aprendiendo** | **[`docs/GUIA.md`](docs/GUIA.md)** — recorrido guiado del código en 6 sesiones, con preguntas, respuestas y experimentos para romperlo a propósito |
| 🤖 **un agente retomando el trabajo** | **[`PROJECT_STATE.md`](PROJECT_STATE.md)** — estado real, decisiones de arquitectura y handoff |

### Proyecto hermano: AgentForge Academy

👉 **https://agentforge-academy-chi.vercel.app**

La academia interactiva con los conceptos que este proyecto implementa: 8 mundos, 56 clases,
del agent loop a los evals y los sistemas multi-agente.

LocalForge es esa teoría hecha código. `docs/GUIA.md` cruza cada decisión del código con su clase
correspondiente.

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

Todo por variables de entorno. Precedencia: **shell > `.env` > default del código**.

```bash
cp .env.example .env    # y ajustalo a tu maquina
```

Los defaults del código son neutrales a proposito: lo que depende del hardware (qué modelo tenés
instalado, cuántos turnos y cuánto tiempo necesita) vive en el `.env`, que no se commitea.
Del `.env` sólo se leen las claves con prefijo `LOCALFORGE_` — el agente corre *sobre* otros
repositorios, y esos repos tienen su propio `.env` con secretos ajenos.

`uv run localforge health` imprime la configuración efectiva y de qué archivo salió.

| Variable | Default | Qué controla |
|---|---|---|
| `LOCALFORGE_PROVIDER` | `ollama` | Backend de inferencia |
| `LOCALFORGE_MODEL` | `qwen3:14b` | Modelo local |
| `LOCALFORGE_NUM_CTX` | `32768` | Ventana de contexto pedida a Ollama |
| `LOCALFORGE_MAX_TURNS` | `20` | Límite de turnos del loop |
| `LOCALFORGE_WALL_CLOCK_S` | `300` | Presupuesto de tiempo por task |
| `LOCALFORGE_TOOL_TIMEOUT_S` | `30` | Timeout por tool |
| `LOCALFORGE_REQUEST_TIMEOUT_S` | `180` | Timeout de una llamada al modelo |
| `LOCALFORGE_TOKEN_BUDGET` | `200000` | Presupuesto de tokens por task |
| `LOCALFORGE_TOOL_OUTPUT_LIMIT` | `8000` | Chars de un tool result que entran al contexto |

**El modelo tiene que soportar tool calling.** Para verificarlo: `ollama show <modelo>` debe
listar `tools` en *Capabilities*.

## Arquitectura

```
usuario → CLI → AgentHarness → ModelProvider → LLM local
                     ↓
                ToolExecutor → tools (list_files, search_code, read_file)
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
