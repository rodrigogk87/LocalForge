# LocalForge — Project State

> **Fuente de verdad del proyecto.** Si sos un agente retomando este trabajo sin haber visto
> la conversación previa, leé este archivo entero antes de tocar código.
> Última actualización: **2026-09-19**

---

## Objetivo del proyecto

Construir un **coding agent que corre sobre un LLM local** (Ollama en una RTX 4090 24 GB),
implementando un **agent harness propio y explícito**.

El usuario selecciona un repositorio y le da instrucciones como:

```
"Analizá este proyecto y explicame su arquitectura."
"Encontrá por qué falla este test."
"Arreglá este bug."
```

El agente inspecciona el repo, llama tools, (más adelante) modifica archivos y ejecuta tests,
verifica resultados y devuelve evidencia.

### Qué NO estamos construyendo

- **No** usamos LangChain, CrewAI ni ningún framework que esconda el agent loop.
  Librerías de infraestructura (httpx, pydantic, FastAPI) sí.
- **No** hay `FakeModelProvider` en el producto. Desde la v0 hay inferencia local real.
  (Sí hay un `ScriptedProvider` **en los tests** — ver "Decisiones de arquitectura".)
- **No** avanzamos a sandbox / MCP / multi-agent / Temporal hasta que lo anterior funcione.
- **No** invertimos en UI hasta que el harness funcione. Hoy la interfaz es una CLI.

---

## Estado actual

**Fase 1 (Agent Foundations) — implementada, pendiente de verificación end-to-end contra el modelo real.**

### Qué funciona hoy

| Capacidad | Estado |
|---|---|
| Modelo de datos completo (Pydantic) | ✅ |
| `ModelProvider` como Protocol | ✅ |
| `OllamaProvider` contra `/api/chat` real | ✅ implementado, health verificado |
| Tools `list_files` y `read_file` | ✅ con tests |
| Validación de argumentos con Pydantic | ✅ |
| Agent loop con tool calling | ✅ con tests |
| Correlación por `call_id` | ✅ |
| 5 condiciones de terminación | ✅ con tests |
| Truncado de tool results | ✅ |
| Path traversal bloqueado | ✅ con tests |
| CLI (`health`, `ask`) | ✅ |
| Suite de tests | ✅ 24 passed |

### Qué NO funciona / no existe todavía

- **Verificación end-to-end con el LLM real: PENDIENTE.** El modelo `qwen3:14b` estaba
  descargándose al escribir esto. `localforge health` pasa, pero todavía no se corrió
  `localforge ask` contra un repo real. **Este es el próximo paso obligatorio.**
- No hay `write_file`, `run_command`, `run_tests`, `git_diff`.
- No hay ContextBuilder, budgets de contexto ni compactación (Fase 2).
- No hay state machine, planner, verifier ni repair loop (Fase 3).
- No hay skills ni MCP (Fase 4).
- No hay permisos, sandbox ni aprobación humana (Fase 5).
- No hay persistencia, checkpoints ni recovery (Fase 6). **Nada sobrevive al proceso.**
- No hay evals (Fase 7).
- No hay API HTTP (FastAPI) ni UI.

---

## Arquitectura actual

```
usuario
  ↓
CLI (localforge ask <repo> "<objetivo>")
  ↓
AgentHarness.run(task)            ← el while con presupuesto
  ├─ build_system_prompt()        ← contexto (trivial por ahora)
  ├─ ModelProvider.complete()     ← Protocol; hoy OllamaProvider
  │     ↓
  │  LLM local (Ollama @ 11434)
  │     ↓ tool_calls
  ├─ ToolExecutor.run_all()       ← resuelve, valida, ejecuta, trunca
  │     ↓
  │  tools (list_files, read_file)
  │     ↓ ToolResult
  └─ correlación por call_id → vuelve al contexto → siguiente turno
  ↓
AgentOutcome (status, reason, turnos, tokens, trayectoria, turn_records)
```

**Invariante central:** el harness no conoce a Ollama. Habla contra el `Protocol`
`ModelProvider`. Agregar `LlamaCppProvider` / `OpenAIProvider` no toca el loop.

**Invariante de tools:** el modelo **propone**, el harness **dispone**. El LLM nunca ejecuta.
Esa asimetría es el punto donde en la Fase 5 se enchufan permisos y sandbox sin reescribir nada.

---

## Estructura relevante del repositorio

```
LocalForge/
├── PROJECT_STATE.md              ← este archivo (fuente de verdad)
├── README.md                     ← uso e instalación
├── pyproject.toml                ← deps, gestionado con uv
├── .env.example                  ← todas las variables de configuración
├── src/localforge/
│   ├── config.py                 Settings desde env vars. Sin magia.
│   ├── models.py                 TODO el modelo de datos. Leer PRIMERO.
│   ├── cli.py                    CLI + ConsoleSink (observabilidad del loop)
│   ├── providers/
│   │   ├── base.py               Protocol ModelProvider + ProviderError
│   │   ├── ollama.py             ÚNICO módulo que conoce la API de Ollama
│   │   └── __init__.py           build_provider(): nombre → implementación
│   ├── tools/
│   │   ├── base.py               Tool Protocol, ToolRegistry, ToolExecutor
│   │   ├── fs.py                 list_files, read_file, safe_path
│   │   └── __init__.py           default_registry()
│   └── harness/
│       ├── loop.py               EL AGENT LOOP. El corazón del proyecto.
│       └── prompt.py             system prompt (futuro ContextBuilder)
├── tests/
│   ├── test_tools.py             13 tests: traversal, paginación, errores
│   └── test_loop.py              11 tests: ciclo, terminación, feedback
└── docs/dev-log/                 detalle histórico (PROJECT_STATE queda compacto)
```

**Orden de lectura recomendado para un agente nuevo:**
`models.py` → `harness/loop.py` → `tools/base.py` → `providers/ollama.py`

---

## Modelo local

| | |
|---|---|
| Provider | Ollama 0.34.2 |
| Endpoint | `http://localhost:11434`, `/api/chat` (nativo, no el compat de OpenAI) |
| Modelo | `qwen3:14b` (~9.3 GB) |
| Hardware | RTX 4090, 24 GB VRAM |
| `num_ctx` | 32768 (explícito en cada request) |
| Binario | `%LOCALAPPDATA%\Programs\Ollama\ollama.exe` (no está en PATH) |

### Limitaciones encontradas

1. **Ollama no devuelve `id` en las tool calls.** La API de OpenAI/Anthropic sí. Como el harness
   correlaciona resultados por `call_id`, **`OllamaProvider` sintetiza ids** (`call_001_a1b2c3`).
   Es el borde correcto: el harness pide una garantía y el adapter la cumple como puede.
2. **`num_ctx` por defecto es chico** y si el prompt lo excede **Ollama trunca en silencio por la
   izquierda**, comiéndose el system prompt. Por eso se fija explícito en cada request.
3. **qwen3 es un modelo razonador**: piensa por defecto, lo que agrega mucha latencia por turno y
   manda el razonamiento a `message.thinking` en vez de `content`. El provider envía `think: false`
   y, si el modelo no lo soporta, reintenta sin el campo (degradación automática).
4. **Ollama manda `done_reason: "stop"` aunque haya pedido tools.** El provider deriva
   `StopReason.TOOL_USE` de la presencia de tool_calls, no del `done_reason`.
5. Algunos modelos devuelven `arguments` como **string JSON** en vez de objeto. El provider
   lo coerciona (`_coerce_args`).

---

## Agent Loop

`src/localforge/harness/loop.py` → `AgentHarness.run(task)`

### Cómo funciona

1. Valida que el repo exista. Construye `ToolExecutor` y system prompt.
2. Estado local explícito: `messages`, `tokens_in/out`, `seen` (Counter), `trajectory`, `records`.
   **Esta tupla es exactamente lo que en la Fase 6 se serializa en un checkpoint.**
3. Por turno:
   - chequea presupuestos **antes** de gastar (wall clock, tokens)
   - `provider.complete()` con `wait_for(min(request_timeout, remaining))` ← composición de budgets
   - acumula tokens **en todos los caminos**
   - si no hay tool calls → si `max_tokens` pide continuación, si no → COMPLETED
   - detecta loops: hash de `(name, arguments)` con `sort_keys`; 3 repeticiones → corta
   - agrega el mensaje del assistant **antes** de los resultados (invariante del protocolo)
   - ejecuta tools en paralelo, correlaciona **por call_id**, assertion de que no falte ninguno
4. Si sale del `for` → `FAILED(max_turns)`.

### Condiciones de terminación (5)

| # | Reason | Implementado |
|---|---|---|
| 1 | éxito (sin tool calls) | ✅ |
| 2 | `MAX_TURNS` | ✅ |
| 3 | `WALL_CLOCK` | ✅ |
| 4 | `TOKEN_BUDGET` | ✅ |
| 5 | `LOOP_DETECTED` | ✅ |
| — | `PROVIDER_ERROR` | ✅ |

### Qué falta

- `max_repairs` (llega con el Verifier, Fase 3)
- estados intermedios (PLANNING, WAITING_TOOL, VERIFYING) — hoy `AgentStatus` tiene 5 valores
- checkpoints por turno (Fase 6)
- hooks de ciclo de vida (Fase 3) — hoy hay un `on_event` simple para observabilidad

---

## Tools disponibles

### `list_files`

| | |
|---|---|
| Propósito | Árbol del repo para orientarse. Barato; se usa primero. |
| Args | `path: str = ""`, `max_depth: int = 3` (1-10), `max_entries: int = 300` (1-2000) |
| Permisos | Solo lectura. Contenido en el workspace por `safe_path`. |
| Implementación | `tools/fs.py::ListFilesTool`. `rglob` + ignore set. |
| Limitaciones | **No respeta `.gitignore`** (usa una lista fija: `.git`, `node_modules`, `.venv`, binarios). Mejora futura: `git ls-files` cuando haya repo git. |

### `read_file`

| | |
|---|---|
| Propósito | Contenido de un archivo, numerado por líneas. |
| Args | `path: str` (requerido), `offset: int = 0`, `limit: int = 400` (1-2000) |
| Permisos | Solo lectura. `safe_path` bloquea traversal. |
| Implementación | `tools/fs.py::ReadFileTool` |
| Limitaciones | Solo UTF-8. Un binario da error explícito. Truncado a `tool_output_limit` (8000 chars) con instrucciones de cómo seguir. |

**Diseño de errores:** todo error de tool vuelve al modelo como `ToolResult(success=False)`,
nunca como excepción, e **incluye qué hacer al respecto** (tool desconocida → lista las válidas;
archivo inexistente → lista los vecinos del directorio).

---

## Persistencia

**No existe.** Todo vive en memoria durante una ejecución de la CLI.
Si el proceso muere, se pierde absolutamente todo. Es la Fase 6.

El estado del loop ya está identificado y es serializable (ver "Agent Loop" paso 2).

---

## Context Management

**Hoy es trivial y hay que medirlo antes de optimizarlo** (decisión explícita del usuario:
observar el problema real antes de implementar la solución).

- El system prompt se regenera por task y se reenvía entero en cada turno.
- Los mensajes se acumulan sin límite.
- Los tool results se truncan a 8000 caracteres **en el executor** (única defensa actual).
- `num_ctx=32768`.

**No existe:** ContextBuilder, presupuesto por capa, estimación de tokens, selección por
relevancia, retrieval, compactación, aislamiento de contexto.

**Señal a vigilar para disparar la Fase 2:** `input_tokens` por turno acercándose a `num_ctx`,
o degradación de calidad en tareas de muchos turnos.

---

## Seguridad

**Estado: mínimo deliberado, y el agente es de solo lectura.**

- ✅ `safe_path()` con `resolve()` antes de comparar → path traversal bloqueado y testeado.
- ✅ Solo hay tools de lectura. No puede escribir ni ejecutar comandos.
- ✅ El modelo nunca ejecuta: propone, el harness ejecuta.
- ❌ No hay ALLOW/ASK/DENY, sandbox, límites de recursos ni aprobación humana.

**Riesgo conocido y aceptado hoy:** el contenido de los archivos que lee entra al contexto sin
delimitar. Un repo hostil podría intentar prompt injection. Hoy el daño posible es acotado
porque **no hay ninguna tool con efectos**. Esto deja de ser aceptable en el momento exacto en que
se agregue `write_file` o `run_command` — **no agregar esas tools sin permisos** (Fase 5).

---

## Verificación

**No existe verificación de resultados.** Hoy, cuando el modelo deja de pedir tools, el harness
acepta su respuesta como `COMPLETED`.

Eso es correcto para tareas de solo lectura ("explicame el repo"), donde no hay nada que verificar
más allá del texto. **Deja de ser correcto en cuanto el agente modifique archivos.**

Lo que sí se verifica hoy: los argumentos de cada tool call contra su schema Pydantic.

Fase 3: tests, lint, types, schemas, git diff + repair loop con `max_repairs`.

---

## Durable execution

**No existe.** Sin checkpoints, sin recovery, sin idempotencia, sin queue.
Un crash pierde la ejecución completa. Es la Fase 6.

---

## Evals

**No existen.** Hay 24 tests unitarios, que no son lo mismo: testean el harness, no la
**calidad del agente**. Fase 7.

---

## Decisiones de arquitectura

### 2026-09-19 — Ollama como primer provider, detrás de un Protocol

**Problema:** hace falta inferencia local real desde el día 0, sin acoplar el harness a un backend.

**Decisión:** `ModelProvider` como `typing.Protocol` de **un solo método**. `OllamaProvider` es la
única implementación y el único módulo que conoce la API de Ollama.

**Motivo:** structural typing desacopla en la dirección correcta — el adapter no importa nada del
harness. Una interface de un método es trivial de implementar para llama.cpp, y trivial de
falsificar en tests.

**Alternativas:** ABC (obliga a heredar, acopla); usar el endpoint OpenAI-compatible de Ollama
(`/v1/chat/completions`) que sí devuelve ids de tool call.

**Consecuencias:** hay que sintetizar `call_id` en el provider. A cambio tenemos acceso a
`options.num_ctx` y `think`, que el endpoint compat no expone igual.

---

### 2026-09-19 — `qwen3:14b` y no `qwen2.5-coder:32b`

**Problema:** en 24 GB de VRAM compiten el tamaño del modelo y la ventana de contexto.

**Decisión:** `qwen3:14b` (~9 GB), dejando ~14 GB para KV cache.

**Motivo:** mientras se construye el harness importa más la **fiabilidad del tool calling** y poder
iterar rápido que el último punto de calidad de código. Un coder-32b deja ~4 GB para contexto, que
es poco para un agente que lee archivos.

**Alternativas:** `qwen2.5-coder:32b` (mejor código, contexto chico), `llama3.1:8b` (más rápido,
tool calling más frágil), `devstral:24b`.

**Consecuencias:** peor generación de código que un coder dedicado. Mitigado porque el modelo es
una variable de entorno: cambiarlo no toca código. **Reevaluar cuando empiece la Fase 3** (escritura
de código de verdad).

---

### 2026-09-19 — `ScriptedProvider` en tests, nunca en el producto

**Problema:** la regla del proyecto prohíbe `FakeModelProvider`. Pero la lógica más crítica del
harness (manejo de fallos, terminación) necesita determinismo para testearse.

**Decisión:** el producto habla siempre con un LLM real. Los tests usan `ScriptedProvider`,
definido **dentro de `tests/test_loop.py`**, nunca en `src/`.

**Motivo:** la regla es sobre el producto. Testear "si el modelo repite la misma tool 3 veces, ¿el
loop corta?" contra un modelo no determinista es imposible, y sin eso esa lógica quedaría sin tests.

**Consecuencias:** hay que sostener la disciplina de que ningún import de `src/` toque ese provider.

---

### 2026-09-19 — El error de tool vuelve al modelo, no propaga

**Problema:** un `FileNotFoundError` que propaga mata una task por algo perfectamente recuperable.

**Decisión:** casi todo error se convierte en `ToolResult(success=False, error=...)` que vuelve al
contexto. El mensaje **incluye qué hacer** (tools válidas, archivos vecinos).

**Motivo:** un agente de 20 pasos con excepciones propagando tiene 20 oportunidades de morir. El
modelo se autocorrige si le das la información.

**Consecuencias:** el harness no puede distinguir "la tool falló" de "la tool devolvió algo feo"
sin mirar `success`. Aceptado. Los fallos **no** recuperables (credenciales, presupuesto) sí cortan.

---

## Bugs / problemas conocidos

1. **El binario de Ollama no está en el PATH del shell.** Está en
   `%LOCALAPPDATA%\Programs\Ollama\ollama.exe`. El servidor corre igual (se autoarranca), y
   LocalForge habla por HTTP, así que no afecta a la app — solo a comandos manuales `ollama ...`.
2. **No se verificó end-to-end contra el modelo real todavía** (descarga en curso). Riesgo abierto:
   que `qwen3:14b` emita tool calls con un formato que el provider parsee mal.
3. `list_files` no respeta `.gitignore`.
4. `EventSink` está declarado como clase-protocolo pero se usa como callable suelto; funciona,
   pero es ruido de tipos que conviene limpiar.

---

## Trabajo completado recientemente

- Entorno: Ollama 0.34.2 instalado vía winget; verificado que la API responde.
- Estructura del proyecto con `uv` (Python 3.14.7).
- `models.py`: AgentTask, AgentMessage, ToolCall/Definition/Result, ModelResponse,
  AgentOutcome, TurnRecord, AgentStatus, StopReason, FailureReason.
- `providers/`: Protocol + OllamaProvider real (incluye síntesis de call_id y fallback de `think`).
- `tools/`: registry, executor con timeout/paralelismo/truncado, `list_files`, `read_file`,
  `safe_path`.
- `harness/loop.py`: agent loop con 5 condiciones de terminación.
- `cli.py`: `health` y `ask` con observabilidad en vivo del loop.
- 24 tests, todos en verde.

---

## Trabajo actual

**Descarga de `qwen3:14b` en curso** (~6 de 9.3 GB al momento de escribir).

Inmediatamente después: correr `localforge ask` contra un repositorio real y comprobar el
recorrido completo `usuario → harness → LLM → tool call → harness ejecuta → ToolResult → LLM →
respuesta final`. **Nada más debe implementarse antes de que eso funcione.**

Archivos involucrados: ninguno a medio escribir. El código está completo y con tests.

---

## Próximos pasos

1. **[BLOQUEANTE]** Terminar descarga → `localforge health` → `localforge ask` sobre un repo real.
2. Si el tool calling de `qwen3:14b` falla o es errático: ajustar el system prompt, y si no alcanza
   probar otro modelo (`qwen2.5-coder`, `mistral-small`, `llama3.1`). **Registrar el hallazgo acá.**
3. Medir: tokens por turno, latencia por turno, cuántos turnos usa para "explicame el repo".
   Esos números deciden cuándo arranca la Fase 2.
4. Agregar `search_code` (grep léxico). Es la tool que más sube la tasa de éxito en repos grandes.
5. Recién ahí: Fase 2 (ContextBuilder) **o** `write_file` + permisos (Fase 5) según lo que duela.
   **No agregar tools con efectos sin permisos.**

---

## Comandos útiles

```bash
# --- Ollama ---
# el binario no esta en PATH:
"$LOCALAPPDATA/Programs/Ollama/ollama.exe" list
"$LOCALAPPDATA/Programs/Ollama/ollama.exe" pull qwen3:14b
curl -s http://localhost:11434/api/version

# --- LocalForge ---
cd ~/Desktop/LocalForge
uv sync --extra dev
uv run localforge health
uv run localforge ask . "Explicame este proyecto"
uv run localforge ask ../agent-harness-lab "¿Cómo está organizado el contenido?" -v
uv run pytest -q
```

---

## Handoff para el siguiente agente

**Si estás retomando este proyecto, empezá por:**

1. Leer `src/localforge/models.py` y `src/localforge/harness/loop.py`. Son ~450 líneas y contienen
   todo el diseño. Los comentarios explican el *por qué* de cada decisión.
2. Correr `uv run pytest -q` — deben pasar 24 tests.
3. Correr `uv run localforge health` — debe reportar el modelo instalado.

**Último objetivo:** demostrar el recorrido end-to-end con LLM local real.

**Último cambio exitoso:** agent loop completo con 24 tests en verde.

**Problema actual:** el end-to-end contra el modelo real **no está verificado**. La descarga de
`qwen3:14b` estaba en curso. Es lo primero que hay que hacer.

**Próxima acción recomendada:**

```bash
uv run localforge ask . "Explicame la arquitectura de este proyecto" -v
```

Observar: ¿pide `list_files` primero? ¿los argumentos llegan bien formados? ¿cuántos turnos usa?
¿el resultado de la tool vuelve correctamente al contexto? Si algo falla, el problema casi seguro
está en `providers/ollama.py::complete` (parseo de tool_calls) o en el system prompt
(`harness/prompt.py`).

**Regla del proyecto:** no agregar features nuevas hasta que ese recorrido funcione de verdad.
