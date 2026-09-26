# LocalForge — Project State

> **Fuente de verdad del proyecto.** Si sos un agente retomando este trabajo sin haber visto
> la conversación previa, leé este archivo entero antes de tocar código.
> Última actualización: **2026-09-26**

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

**Fase 1 (Agent Foundations) — COMPLETA Y VERIFICADA END-TO-END contra el LLM local real.**

**Fase 3 (Harness Engineering) — máquina de estados, verifier y repair loop implementados.**
Falta planner (deliberado: ver más abajo) y hooks de ciclo de vida.

**Fase 2 (Context Engineering) — primera porción implementada y testeada.** ContextBuilder,
presupuesto por capa, estimador calibrado y compactación. Falta retrieval, que está bloqueado por
`search_code`. Verificada con tests y con `ScriptedProvider`; **todavía no corrida contra el LLM
local** (ver "Trabajo actual").

El recorrido `usuario → harness → LLM local → tool call → harness ejecuta → ToolResult → LLM →
respuesta final` funciona. Evidencia reproducible más abajo.

### Qué funciona hoy

| Capacidad | Estado |
|---|---|
| Modelo de datos completo (Pydantic) | ✅ |
| `ModelProvider` como Protocol | ✅ |
| `OllamaProvider` contra `/api/chat` real | ✅ **verificado con inferencia real** |
| Tools `list_files`, `search_code` y `read_file` | ✅ con tests |
| Validación de argumentos con Pydantic | ✅ |
| Agent loop con tool calling | ✅ **verificado end-to-end** |
| **Tool calls en paralelo** | ✅ 3 `read_file` en un turno, concurrentes |
| Correlación por `call_id` | ✅ |
| 5 condiciones de terminación | ✅ con tests |
| Truncado de tool results | ✅ |
| Path traversal bloqueado | ✅ con tests |
| CLI (`health`, `ask`) | ✅ |
| **ContextBuilder con presupuesto por capa** | ✅ Fase 2, con tests |
| **Compactación de observaciones, nunca en silencio** | ✅ con tests |
| **Estimador de tokens calibrado contra `prompt_eval_count`** | ✅ con tests |
| **Máquina de estados con transiciones prohibidas** | ✅ Fase 3, con tests |
| **Verifier de trayectoria + repair loop** | ✅ Fase 3, con tests |
| `CONTEXT_OVERFLOW` corta limpio | ✅ |
| Suite de tests | ✅ 85 passed |

### Evidencia de la verificación (2026-09-19)

Comando: `uv run localforge ask . "Explicame la arquitectura de este proyecto..."`

```
[   0.0s] ── turno 1
[   0.7s]   modelo: tool_use · 850→35 tok | 0.7s
[   0.7s]   → list_files(max_depth=5, max_entries=100, path='.')
[   0.8s]   ✓ list_files 564 chars
[   0.8s] ── turno 2
[   2.1s]   modelo: tool_use · 1134→117 tok | 1.4s
[   2.1s]   → read_file(path='src/localforge/cli.py', limit=100, offset=0)
[   2.1s]   → read_file(path='src/localforge/models.py', limit=100, offset=0)
[   2.1s]   → read_file(path='src/localforge/harness/loop.py', limit=100, offset=0)
[   2.1s]   ✓ read_file 3888 chars   ← los tres completan a la vez:
[   2.1s]   ✓ read_file 3735 chars      ejecucion paralela confirmada
[   2.1s]   ✓ read_file 4248 chars
[   2.1s] ── turno 3
[  12.8s]   modelo: end_turn · 4975→835 tok | 10.7s

── completed | 3 turnos | 7946 tokens | 12.8s | tools: list_files, read_file, read_file, read_file
```

En la respuesta el modelo nombra `AgentHarness`, `StopReason`, `ToolCall`, `ToolResult` y la
integración con Ollama: información que **sólo se obtiene leyendo los archivos**, no del listado.

### Hallazgo importante: grounding

La **primera** corrida (con el system prompt original) terminó en 2 turnos llamando sólo a
`list_files` y **sin leer ningún archivo**. Respondió infiriendo de los nombres, con lenguaje
especulativo ("posiblemente", "probablemente"), y concluyó que `cli.py` era el archivo más
importante **por su tamaño en KB**.

El mecanismo del harness funcionó perfecto; lo que falló fue el grounding del modelo.
En la taxonomía de fallos eso es `CONTEXT`/`MODEL`, no `TOOL` ni `EXECUTION`.

**Arreglo aplicado** (`harness/prompt.py`): el prompt pasó de "no inventes" a un método explícito
en 3 pasos con read_file obligatorio, más una regla accionable — *prohibido usar "posiblemente",
"probablemente", "parece que"; si te sale esa palabra es la señal de que te falta un read_file*.

**Resultado:** 0 → 3 archivos leídos, y el hedging sobrevive **sólo** sobre archivos que
efectivamente no leyó (comportamiento epistémicamente correcto, no alucinación).

**Lección para la Fase 3:** el prompt mejora el grounding pero no lo garantiza. La garantía real es
un **verifier de trayectoria**: rechazar la respuesta final si el agente no leyó ningún archivo.

### Qué NO funciona / no existe todavía

- No hay `write_file`, `run_command`, `run_tests`, `git_diff`.
- No hay retrieval ni selección por relevancia (resto de la Fase 2).
- No hay planner ni hooks de ciclo de vida (resto de la Fase 3).
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
  ├─ build_system_prompt()        ← una capa del contexto: `instructions`
  ├─ ContextBuilder.build()       ← asigna el presupuesto y compacta si no entra
  │     ↓                            state (messages) → context (built.messages)
  ├─ ModelProvider.complete()     ← Protocol; hoy OllamaProvider
  │     ↓
  │  LLM local (Ollama @ 11434)
  │     ↓ tool_calls
  ├─ ToolExecutor.run_all()       ← resuelve, valida, ejecuta, trunca
  │     ↓
  │  tools (list_files, read_file)
  │     ↓ ToolResult
  ├─ correlación por call_id → vuelve al state → siguiente turno
  └─ observe_actual()             ← calibra el estimador con prompt_eval_count
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

### Segunda máquina verificada (2026-09-20) — MacBook Pro M1 Pro

El proyecto corre entero en un Mac sin tocar código: 24/24 tests, `health` y `ask` end-to-end.

| | |
|---|---|
| Provider | Ollama 0.31.2 |
| Modelo | `gemma4:e4b` (9.6 GB en disco, **3.3 GB residentes**) |
| Hardware | M1 Pro, 16 GB unified memory, 8 cores |
| Carga | **100% GPU**, `num_ctx` 32768 completo |
| Python | 3.14.6 vía `uv` |

`gemma4:e4b` es MatFormer E4B: 8B de parámetros en disco pero sólo ~4B activos, así que entra
cómodo en 16 GB compartidos. Soporta `tools`, que es el requisito duro (`ollama show` lo lista).

**Diferencia de comportamiento, no sólo de velocidad.** Misma tarea ("explicame la arquitectura"):

| | RTX 4090 · qwen3:14b | M1 Pro · gemma4:e4b |
|---|---|---|
| Turnos | 3 | **11** |
| Tokens | 7.946 | **67.011** |
| Tiempo | 12,8s | **126s** |
| `read_file` por turno | 3 en paralelo | **1** |

qwen3 pide tres `read_file` en un turno; gemma4 pide uno. El paralelismo del executor queda sin
usar y el costo por tarea crece linealmente con la cantidad de archivos. **Por eso `max_turns=20`
y `wall_clock_s=300` son números de la 4090**: en esta máquina se agotan antes de terminar en
cualquier repo mediano. Ver `.env` (no commiteado): 35 turnos y 900s.

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

### Condiciones de terminación (7)

| # | Reason | Implementado |
|---|---|---|
| 1 | éxito (sin tool calls) | ✅ |
| 2 | `MAX_TURNS` | ✅ |
| 3 | `WALL_CLOCK` | ✅ |
| 4 | `TOKEN_BUDGET` | ✅ |
| 5 | `LOOP_DETECTED` | ✅ |
| 6 | `VERIFICATION_FAILED` | ✅ Fase 3 |
| 7 | `CONTEXT_OVERFLOW` | ✅ |
| — | `PROVIDER_ERROR` | ✅ |

### Qué falta

- planner (estado `PLANNING` y su transición; hoy no existe a propósito)
- checkpoints por turno (Fase 6)
- hooks que puedan **vetar**, no sólo observar (Fase 5) — hoy `on_event` sólo observa

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

### `search_code`

| | |
|---|---|
| Propósito | Encontrar **dónde** está algo. Es el paso que faltaba entre orientarse y leer. |
| Args | `pattern: str` (requerido), `path`, `regex=False`, `case_sensitive=False`, `glob`, `context_lines=0` (0-5), `max_results=60` (1-500) |
| Permisos | Solo lectura. `safe_path` bloquea traversal. |
| Implementación | `tools/search.py::SearchCodeTool`. Literal por defecto, regex opcional. Mismo ignore set que `list_files`; salta binarios y archivos > 2 MB. |
| Limitaciones | Grep léxico, no semántico: encuentra el texto, no el concepto. No respeta `.gitignore`. Líneas recortadas a 400 chars. |

**Por qué existe, y es el mejor ejemplo del proyecto de "la tool que falta no se arregla con prompt":**
en el M1, ante *"¿dónde se valida que una ruta no escape del workspace?"*, el agente listó el árbol,
abrió `cli.py` (mal), lo leyó dos veces, nunca encontró `safe_path` en `tools/fs.py` y contestó con
*"el más probable lugar"* — la palabra que el prompt prohíbe. El prompt no era el problema: no
existía la herramienta para responder esa pregunta. Hoy `search_code(pattern="safe_path")` devuelve
`src/localforge/tools/fs.py:31  def safe_path(...)` en una llamada.

El orden del registry es deliberado: `list_files` → `search_code` → `read_file`, que son las tres
operaciones de una investigación de código en el orden en que se usan.

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

**Fase 2 — primera porción implementada (2026-09-26).** `src/localforge/harness/context.py`.

El contexto dejó de ser una lista que crece y pasó a ser una **asignación que se recalcula por
turno**. El loop sigue siendo dueño del state completo (`messages`); lo que viaja al provider es
`built.messages`, una proyección de ese state que entra en el presupuesto. Esa distinción
(**state ≠ context**) es la que permite que la Fase 6 serialice el state entero sin que el
contexto crezca con él.

### Las cuatro piezas

| Pieza | Qué hace |
|---|---|
| `TokenEstimator` | Estima por longitud (3.6 chars/token) y **se calibra** con `prompt_eval_count`, el conteo real del tokenizer que Ollama devuelve. Arranca heurística, termina medición. EMA con α=0.25: converge despacio a propósito. |
| `ContextBreakdown` | Tokens por capa, con los nombres de W2·C8. Las cuatro que no existen (`environment`, `skills`, `memory`, `retrieved`) se reportan con `present=False`: la tabla también es el backlog. |
| `ContextBudget` | `available = num_ctx - reserve_output`. Reservar lugar para la salida no es opcional: la ventana se comparte entre leer y escribir. |
| `ContextBuilder` | Arma el contexto del turno y compacta si no entra. |

### Compactación

Se compactan **observaciones** (rol `tool`), de la más vieja a la más nueva, **nunca en silencio**:
cada una deja un marcador que dice qué tool era, cuántos caracteres había y cómo recuperarlos. Es
la misma regla que `ToolExecutor._truncate` de la Fase 1.

Nunca se toca la task, ni el razonamiento del assistant, ni los últimos `keep_recent_messages`.
El criterio es **valor por token**: un `read_file` de 4000 chars ya cumplió su función porque el
modelo extrajo la conclusión, que son 40 tokens. Lo caro es lo redundante. El razonamiento es lo
contrario: chico, y único registro de *por qué* el agente hizo lo que hizo — borrarlo lo empuja al
`LOOP_DETECTED`.

### Medición (lo que la Fase 2 exigía antes de optimizar)

Mismo agente, 9 turnos leyendo archivos, con `ScriptedProvider`:

| `num_ctx` | Turno 9 | Compactación |
|---|---|---|
| 32768 | 19446 / 28768 tok (67.6%) | no se activa |
| 9000 | 5684 / 6000 tok (94.7%) | 6 observaciones, −12972 tok |

Sin compactar, con `num_ctx=9000` el turno 9 habría pedido 19446 de 6000 disponibles: **3,2× por
encima**. Con la ventana grande el compactador está en el código y **nunca se ejecuta**, que es lo
correcto.

Reparto típico: **observations 83%**, instructions 2%, tools 1.5%, conversation 1%, task 0.1%.
Acortar el system prompt para ahorrar contexto es trabajar en el lugar equivocado.

### Qué de la Fase 2 NO está

- **Retrieval just-in-time** (W2·C11). Bloqueado por `search_code`: sin búsqueda, traer "el
  fragmento exacto" es imposible porque no se sabe dónde está.
- **Selección por relevancia** (W2·C10). Hoy la única política es "todo menos lo viejo".
- **Aislamiento de contexto** (W2·C13). Necesita subagentes → Fase 8.
- **Compactación por resumen del modelo**. La versión actual es determinista y gratis; la del
  resumen cuesta una llamada extra y conserva más señal.

### El agujero de overflow, tapado

Si el contexto no entra ni compactando todo lo compactable, el loop corta con
`FailureReason.CONTEXT_OVERFLOW` en vez de mandarlo igual y dejar que Ollama trunque en silencio
por la izquierda (comiéndose el system prompt). Hay un margen del 10% porque el total es una
**estimación**: abortar una corrida sana por un 3% de error del estimador sería peor que el
problema que evita.

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

**Fase 3 implementada (2026-09-26).** `src/localforge/harness/verify.py` y `harness/state.py`.

Hasta acá, cuando el modelo dejaba de pedir tools el harness aceptaba su respuesta. O sea que
**el modelo era juez de su propio trabajo.** El verifier mueve esa decisión al harness, que puede
mirar *lo que el agente hizo* en vez de lo que dice que hizo.

### Máquina de estados

`AgentStatus` sumó `WAITING_TOOL`, `VERIFYING` y `REPAIRING`. Lo que la vuelve una máquina no son
los estados: son las **transiciones prohibidas**, que viven en `harness/state.py`.

```
CREATED → RUNNING ⇄ WAITING_TOOL
             ↓
          VERIFYING → COMPLETED
             ↓
          REPAIRING → RUNNING
```

No se puede pasar a `COMPLETED` sin pasar por `VERIFYING`: ese es el punto. Cualquier estado no
terminal puede caer en `FAILED`/`CANCELLED`, porque los presupuestos cortan desde donde sea.

**No existe `PLANNING`, aunque el roadmap lo mencione.** No hay planner. Un estado por el que el
agente pasa sin hacer nada es decoración que miente sobre lo que el sistema hace.

`outcome.state_path` guarda el camino, colapsando ciclos:
`created -> (running -> waiting_tool) x5 -> running -> verifying -> repairing -> running -> verifying -> completed`.

### Los verifiers

| Verifier | Qué rechaza |
|---|---|
| `TrajectoryVerifier` | Una respuesta sin **ninguna** llamada a `read_file` o `search_code`. `list_files` **no cuenta**: un listado dice cómo se llaman las cosas, no qué hacen — y aceptarlo es exactamente el fallo original. |
| `NoHedgingVerifier` | Lenguaje especulativo ("posiblemente", "el más probable") **cuando el agente leyó poco**. Con evidencia abundante se tolera: hedgear sobre lo que no leíste es epistémicamente correcto, no un fallo. |
| `CompositeVerifier` | Corre varios y devuelve el **primer** rechazo. Uno por vez produce una reparación por vez, que es más fácil de verificar después. |

El `feedback` del verdict es texto **dirigido al modelo**: no dice "verificación fallida", dice qué
hacer distinto. Por eso el repair loop puede simplemente reinyectarlo.

### Repair loop

Si el verifier rechaza, el feedback vuelve como un mensaje más y el agente tiene otra oportunidad,
hasta `max_repairs` (default 2). Agotadas, `FAILED(VERIFICATION_FAILED)` — **devolviendo igual la
última respuesta**: es mala, pero el usuario la prefiere a nada, y el `reason` deja claro que no
pasó la verificación.

Apagar la verificación requiere pasar un verifier permisivo explícito. Es deliberado: desactivar
una garantía tiene que ser visible en el código que la desactiva.

### Qué cierra esto, y qué no

**Cierra** el bug conocido #4: el grounding deja de ser probabilístico. El prompt pide y
`search_code` habilita, pero el verifier **garantiza** — es lo único que puede rechazar una
respuesta no fundamentada.

**No cierra** la calidad de la respuesta. El verifier juzga una propiedad *estructural* de la
trayectoria, no si la explicación es buena. Eso necesita model-as-judge (W7·C45) con sus propios
sesgos, y es Fase 7.

### Qué falta de Fase 3

- **Planner.** Hoy el modelo decide su próximo paso turno a turno, sin plan explícito.
- **Hooks de ciclo de vida.** Hay `on_event`, que sólo observa. Un hook que pueda *vetar* (por
  ejemplo, negar una tool call antes de ejecutarla) es la Fase 5.
- **Verifiers de resultado**: tests, lint, types, git diff. Llegan cuando el agente escriba código.

---

## Durable execution

**No existe.** Sin checkpoints, sin recovery, sin idempotencia, sin queue.
Un crash pierde la ejecución completa. Es la Fase 6.

---

## Evals

**No existen.** Hay 85 tests unitarios, que no son lo mismo: testean el harness, no la
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
2. `list_files` no respeta `.gitignore`.
3. `EventSink` está declarado como clase-protocolo pero se usa como callable suelto; funciona,
   pero es ruido de tipos que conviene limpiar.
4. **El grounding depende del modelo, y con uno chico el prompt no alcanza.** En el M1 con
   `gemma4:e4b`, ante *"¿en qué archivo se valida que una ruta no se escape del workspace?"* el
   agente listó el árbol, **adivinó** `cli.py`, lo leyó dos veces, nunca encontró `safe_path`
   (está en `tools/fs.py`) y contestó *"el más probable lugar"* — justo la palabra que el system
   prompt prohíbe.
   **Atacado en dos frentes (2026-09-26):** existe `search_code`, y el system prompt ahora tiene un
   PASO 2 que manda a buscar antes de abrir archivos. Pero sigue siendo probabilístico: el modelo
   *puede* ignorar la tool. **La garantía dura sigue siendo el verifier de trayectoria** (Fase 3),
   que es lo único que puede rechazar una respuesta no fundamentada. Este bug se cierra ahí.

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

**2026-09-20 — configuración por máquina.** El proyecto se corrió en un segundo equipo (M1 Pro) y
eso destapó dos agujeros en la config:

- **El `.env` no se leía.** Había `.env.example` y `.gitignore` excluía `.env`, lo que sugiere
  "copiá el ejemplo y anda" — pero nada en `src/` leía el archivo. `config.py::load_dotenv()` lo
  implementa en ~20 líneas de stdlib (sin `python-dotenv`: sería la primera dependencia que no es
  infraestructura). **Sólo adopta claves con prefijo `LOCALFORGE_`**, porque el agente corre sobre
  otros repos y esos repos tienen su propio `.env` con credenciales. El shell le gana al archivo.
- **El entorno se leía al importar, no al construir.** Los valores vivían en los defaults del
  dataclass, que Python evalúa una sola vez al definir la clase: `Settings()` después de tocar
  `os.environ` devolvía silenciosamente lo de la importación. Ahora hay `Settings.from_env()` y
  los defaults del dataclass son **neutrales** (ninguna máquina en particular). Efecto lateral
  bueno: `Settings()` a secas es hermético, que es justo lo que quiere un test.
- `health` ahora imprime la config efectiva y **de qué archivo salió**: un `.env` que no se está
  leyendo era indistinguible de uno que se lee y dice lo mismo.

---

## Trabajo actual

**Fase 2, primera porción: código completo y 40 tests en verde. Falta UNA cosa:** correrla contra
el LLM local. Se validó con `ScriptedProvider` (determinista, mide el presupuesto y la
compactación) pero no con inferencia real, porque Ollama no estaba corriendo al momento de
escribirla.

**Lo que falta hacer, concretamente:**

```bash
ollama serve                     # o abrir la app
uv run localforge ask . "explicame la arquitectura" -v
```

Y mirar dos cosas: que la línea `ctx:` reporte números coherentes con `input_tokens`, y que el
estimador converja (`chars_per_token` debería moverse desde 3.60 hacia el ratio real de
`gemma4:e4b`). Si el error del estimador queda por debajo del 10% a los pocos turnos, la
calibración funciona.

Último cambio: `harness/prompt.py` reescrito para forzar grounding (ver "Hallazgo importante").

---

## Próximos pasos

Ordenados por lo que más duele hoy, no por el orden de las fases:

1. **Correr lo nuevo contra el LLM local.** El código está y los tests pasan, pero la validación
   con inferencia real falta (ver "Trabajo actual"). Es el paso más chico y el más urgente: hasta
   que no corra, el estimador nunca se calibró contra un tokenizer de verdad.
2. **Decidir la próxima capacidad**, y la decisión tiene una restricción dura:
   `write_file` y `run_command` **no se agregan sin permisos ALLOW/ASK/DENY** (Fase 5). Hoy el
   agente es de solo lectura y por eso el riesgo de prompt injection es acotado. Ese equilibrio se
   rompe exactamente el día que exista una tool con efectos.
3. Permisos ALLOW/ASK/DENY (Fase 5). Es el prerequisito duro de `write_file` y `run_command`.

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

**Último objetivo:** demostrar el recorrido end-to-end con LLM local real. **CUMPLIDO.**

**Último cambio exitoso:** system prompt reescrito para forzar grounding; el agente pasó de leer
0 archivos a leer 3 en paralelo.

**Problema actual:** ninguno bloqueante. Lo que falta es validación con inferencia real: la Fase 2
y `search_code` están escritos y testeados, pero no corridos contra el LLM local.

**Próxima acción recomendada:**

**Primero:** arrancar Ollama y correr `uv run localforge ask . "explicame la arquitectura" -v` para
validar la Fase 2 contra inferencia real. Es lo único que le falta a lo que ya está escrito.

**Después:** los permisos ALLOW/ASK/DENY de la Fase 5, que son el prerequisito duro de cualquier
tool con efectos.

**Restricción dura que NO hay que violar:** no agregar `write_file` ni `run_command` antes de que
exista el sistema de permisos ALLOW/ASK/DENY. Hoy la seguridad del proyecto descansa en que el
agente es de solo lectura.

**Regla del proyecto:** trabajar incrementalmente — diseñar una parte chica, implementarla,
ejecutarla de verdad contra el LLM local, medir, actualizar este archivo, y recién ahí seguir.
