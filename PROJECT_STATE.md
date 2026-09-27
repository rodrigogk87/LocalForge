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

**Fase 4 (Skills) — skills con progressive disclosure implementadas; MCP NO.**

**Fase 8 (Multi-agent) — subagentes con contexto aislado implementados; worktrees y paralelismo NO.**

**Fase 7 (Agent Evals) — dataset, checks deterministas y comparador implementados.** Falta
model-as-judge y una primera corrida real (necesita Ollama).

**Fase 6 (Durable Agents) — checkpoints y resume implementados.** Falta queue/worker y memoria
entre sesiones.

**Fase 5 (Sandbox Engineering) — permisos implementados; sandbox NO.** ALLOW/ASK/DENY con
fail-closed y aprobación humana. Falta el aislamiento real (Docker, límites de recursos, red).

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
| **Permisos ALLOW/ASK/DENY con fail-closed** | ✅ Fase 5, con tests |
| **Secretos (`.env`, claves) no se pueden leer** | ✅ con tests |
| **Aprobación humana por consola** | ✅ |
| **Checkpoints por turno con escritura atómica** | ✅ Fase 6, con tests |
| **`resume` retoma sin re-ejecutar nada** | ✅ con tests |
| **Evals: golden tasks, taxonomía, costo, comparación** | ✅ Fase 7, con tests |
| **Skills con progressive disclosure** | ✅ Fase 4, con tests |
| **Subagentes con contexto aislado** | ✅ Fase 8, con tests |
| **Referencias de las guías verificadas contra el código** | ✅ con tests |
| **8 mundos como proyectos independientes** | ✅ con tests |
| Suite de tests | ✅ 53 en la raíz · 939 sumando los 8 mundos |

### Evidencia de la verificación (2026-09-19)

Comando: `uv run localforge ask . "Explicame la arquitectura de este proyecto..."`

```
[   0.0s] ── turno 1
[   0.7s]   modelo: tool_use · 850→35 tok | 0.7s
[   0.7s]   → list_files(max_depth=5, max_entries=100, path='.')
[   0.8s]   ✓ list_files 564 chars
[   0.8s] ── turno 2
[   2.1s]   modelo: tool_use · 1134→117 tok | 1.4s
[   2.1s]   → read_file(path='localforge/cli/', limit=100, offset=0)
[   2.1s]   → read_file(path='localforge/models.py', limit=100, offset=0)
[   2.1s]   → read_file(path='localforge/harness/loop.py', limit=100, offset=0)
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
- No hay MCP (resto de la Fase 4).
- No hay sandbox, límites de recursos ni políticas de red (resto de la Fase 5).
- No hay queue, workers ni memoria entre sesiones (resto de la Fase 6).
- No hay model-as-judge ni datasets grandes (resto de la Fase 7).
- No hay worktrees ni paralelismo entre subagentes (resto de la Fase 8).
- No hay API HTTP (FastAPI) ni UI.

---

## Arquitectura actual

```
usuario
  ↓
CLI (lfw8 ask <repo> "<objetivo>")   ← un comando por mundo: lfw1..lfw8
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
  │  tools (list_files, search_code, read_file, load_skill, delegate)
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

### El repositorio son ocho proyectos

**2026-09-27.** No hay un codebase único. Hay **ocho proyectos Python independientes**, uno por mundo,
cada uno con el código tal cual estaba al cerrar ese mundo, su propio venv, sus propios tests y su
propio comando.

El motivo: leer el código final para entender el Mundo 1 no funciona. Abrís `models.py` y encontrás
ocho estados cuando en el Mundo 1 había cinco, y tres de ellos hablan de un verifier que todavía no
te explicaron. **Los detalles de los mundos posteriores tienen que ser invisibles mientras leés uno.**

Eso no se resuelve partiendo el código final: es imposible — Python no deja extender un enum y el
loop importa de seis mundos. Se resuelve con ocho proyectos.

```
LocalForge/
├── worlds/
│   ├── 1-foundations-w1/     W1  15 archivos   24 tests   lfw1   foto (7a3295d)
│   ├── 2-context-w2/         W2  19            59         lfw2   foto (49a0aaa)
│   ├── 3-harness-w3/         W3  22            85         lfw3   foto (1c8c985)
│   ├── 4-sandbox-w5/         W5  24           118         lfw5   foto (e2b48f9)
│   ├── 5-durable-w6/         W6  26           133         lfw6   foto (24b24a5)
│   ├── 6-evals-w7/           W7  28           159         lfw7   foto (1808094)
│   ├── 7-skills-w4/          W4  31           177         lfw4   foto (5f62eb4)
│   └── 8-multiagent-w8/      W8  33           184         lfw8   ← CÓDIGO VIVO
├── docs/          GUIA.md · guia-web.html · code-refs.json
├── scripts/       build_worlds.py · sync_code_refs.py
├── tests/         53 tests: coherencia de los ocho + que las guías no mientan
└── pyproject.toml el repo NO es un paquete
```

**Los pasos 1 a 7 son fotos**, generadas desde la historia de git con `scripts/build_worlds.py`. No se
editan: se regeneran. **El paso 8 es el código vivo**, el único que se edita a mano. Si mañana hay un
Mundo 9, se construye ahí y después se saca su foto.

Y no hubo que escribir 25.000 líneas: cada mundo se había cerrado con un commit, así que los
snapshots ya existían.

### El orden es el de construcción, no el del roadmap

El Mundo 5 (permisos) se hizo antes del 4 (skills), porque los permisos eran prerequisito duro de
cualquier tool con efectos. Ordenarlos por número rompería lo que los hace útiles — el paso 4 tendría
skills y el 5 las perdería. Así cada paso es el anterior **más una cosa**, y los tests lo confirman:
crecen 24 → 59 → 85 → 118 → 133 → 159 → 177 → 184 sin que ninguno se rompa.

El único caso que no salió directo de la historia: los Mundos 4 y 8 entraron en el **mismo commit**,
así que el paso 7 es ese commit menos `subagent.py`.

### Qué se eliminó, y por qué

Hubo una etapa intermedia con `packages/`: un workspace de once subproyectos por capacidad
(`core`, `sandbox`, `tools`, `harness`, …) con las dependencias declaradas y verificadas. **Se
eliminó.** Funcionaba y tenía una ventaja real — el límite del paquete encontró que `delegate` no
estaba conectado a la CLI — pero convivía con `worlds/` mostrando el mismo código con dos
organizaciones distintas, y eso confunde más de lo que aporta. Lo que se rescató antes de borrarlo
está en el paso 8: el flag `--delegate` y las anotaciones por mundo de `models.py`.

Queda en la historia (`b160136`) si alguna vez hace falta.

### Los tests de la raíz

`tests/test_worlds.py` verifica la propiedad central con una diagonal: **cada módulo aparece en su
paso y no antes.** `VERIFYING` no está declarado antes del paso 3; el paso 1 tiene exactamente cinco
estados. Más que cada carpeta sea un proyecto de verdad, y que los entrypoints no se repitan.

`tests/test_docs.py` verifica que las guías no mientan: que las líneas que citan correspondan, que
las rutas existan, que el HTML esté bien formado y sin anclas rotas.

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

`localforge/harness/loop.py` → `AgentHarness.run(task)`

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
`localforge/tools/fs.py:31  def safe_path(...)` en una llamada.

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

**Fase 6 implementada (2026-09-26).** `localforge/durable/checkpoint.py`.

Desde la Fase 1 el loop tenía un comentario diciendo que su estado local *"es exactamente lo que en
la Fase 6 se serializa en un checkpoint"*. Se pudo cobrar esa promesa por una sola razón: **el
estado estaba nombrado.** La lista de campos que se restauran es idéntica a la que se inicializa —
esa simetría es la prueba de que el estado del loop está completo.

### Las tres propiedades que separan esto de un `json.dump`

| Propiedad | Por qué |
|---|---|
| **Escritura atómica** | Temporal en el mismo directorio + `fsync` + `os.replace`. Un crash a mitad de escritura deja el checkpoint **anterior** intacto en vez de un archivo truncado. Un checkpoint corrupto es peor que no tenerlo: te hace creer que podés resumir. El `fsync` va antes del rename porque si no, el rename puede llegar al disco antes que el contenido. |
| **Versión de esquema** | El checkpoint que escribe hoy lo lee el proceso de mañana con el código cambiado. Sin versión, un campo renombrado es un crash confuso en el peor momento. Se rechaza explícito y temprano. |
| **Idempotencia** | El checkpoint se graba **al cerrar** el turno, después de aplicar los resultados de las tools. Así, al resumir, nunca se re-ejecuta una tool cuyo resultado ya está guardado. Resumir es seguir, no repetir. |

### Qué se persiste, y por qué cada cosa

Además de lo obvio (`messages`, tokens, `trajectory`, `records`):

- **El `Counter` de detección de loops.** Sin él, al resumir el agente se olvida de que ya repitió
  dos veces la misma llamada y el límite se reinicia: un agente en loop podría girar para siempre
  cruzando reinicios.
- **Los tokens acumulados.** Si se reiniciaran en cada crash, el presupuesto dejaría de ser un
  presupuesto.
- **La calibración del estimador** (`chars_per_token`). Barata de perder, gratis de guardar.
- **El camino de estados.** Para no perder la historia de reparaciones.

### Uso

```bash
uv run localforge ask . "explicame el repo" --save   # imprime el run id
uv run localforge runs                              # lista las corridas
uv run localforge resume <id>                       # retoma
```

La persistencia es **opt-in**: escribir en el disco del usuario no debería ser un efecto silencioso
de correr el agente. Sin `--save`, el comportamiento es idéntico al de antes.

### Qué falta de Fase 6

- **Queue y workers** (W6·C40). Hoy la corrida es un proceso en primer plano.
- **Durable execution** estilo Temporal (W6·C41).
- **Memoria entre sesiones** (las cuatro clases de memoria). El checkpoint es memoria *de una
  corrida*; no hay nada que el agente recuerde de una task a la otra.
- **Recovery automático.** Hoy resumir es manual: alguien corre `resume`.

---

## Context Management

**Fase 2 — primera porción implementada (2026-09-26).** `localforge/context/builder.py`.

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

**Fase 5, permisos: implementados (2026-09-26).** `localforge/sandbox/permissions.py`.

El harness ya tenía la asimetría correcta desde la Fase 1 — **el modelo propone, el harness
ejecuta** — y `tools/base.py` decía en su docstring que ahí se enchufarían los permisos. Se
enchufaron exactamente ahí, en `run_one`, sin que el loop se enterara.

### Las tres decisiones que importan

| Decisión | Por qué |
|---|---|
| **El default nunca es ALLOW** | Una tool que la política no conoce se deniega. Mismo criterio que `ToolResult.success` sin default: en seguridad, el default seguro es el pesimista. Un permiso que falla abierto convierte cada olvido en un agujero. |
| **Un DENY vuelve como `ToolResult`, no como excepción** | El modelo lee "no tenés permiso" y busca otro camino. Matar al agente por una decisión de política sería tratarla como un fallo del sistema. |
| **ASK sin nadie a quien preguntar es DENY** | Sin TTY no hay humano. "No pude preguntar" no puede resolverse como "dale". La elección depende del entorno, no de un flag: en CI, donde más importa, no hay forma de olvidarse del flag seguro. |

### La política por defecto

Se lee de arriba hacia abajo, y el orden **es** el diseño:

1. `DENY` sobre archivos sensibles (`.env`, `*.pem`, `id_rsa*`, `*.tfvars`, `.git-credentials`,
   `service-account*.json`…) **para cualquier tool**, incluidas las de lectura.
2. `ALLOW` para `list_files`, `search_code`, `read_file`.
3. `ASK` para todo lo demás.

El punto 3 es el que da la garantía a futuro: **cuando alguien agregue `write_file` o
`run_command`, va a caer en ASK por construcción**, sin que haya que acordarse de agregarlo acá.

**El DENY sobre secretos no es teórico.** El contenido de lo que el agente lee entra al contexto, y
el contexto viaja al modelo: leer un `.env` es exfiltrarlo. Hoy el modelo es local, y el día que el
provider sea remoto es literal. Hay un test que verifica que el valor del secreto no aparece **en
ninguna parte** de lo que vuelve al modelo, ni siquiera en el mensaje de error.

El motivo del DENY es accionable, no un "prohibido": *"si necesitás saber qué variables usa el
proyecto, buscá dónde se leen en el código (`search_code`) o mirá el `.env.example`"*.

### Aprobación humana

`ConsoleApprover` muestra tool, argumentos y motivo, y espera un sí explícito (`s`/`y`, o `t` para
recordar esa tool en la corrida). Cualquier otra cosa es no: un enter distraído no autoriza una
escritura. Usa `asyncio.to_thread` para no bloquear el loop mientras el humano piensa.

`localforge ask --read-only` deniega todo lo que no sea lectura sin preguntar.

### Qué falta de Fase 5 — y es la mitad importante

**No hay sandbox.** Un permiso decide *si* se ejecuta; un sandbox contiene *lo que pasa* cuando se
ejecuta. Falta todo eso:

- ❌ Aislamiento de filesystem real (hoy sólo `safe_path`, que es validación, no contención)
- ❌ Docker / contenedores (W5·C30)
- ❌ Límites de CPU, memoria y procesos (W5·C32)
- ❌ Políticas de red (W5·C33)

**Por eso `run_command` sigue sin agregarse**, aunque los permisos ya existan. Ejecutar comandos
arbitrarios sin contención es el agujero que ningún ALLOW/ASK/DENY tapa: una vez que el comando
corre, el permiso ya hizo todo lo que podía hacer.

`write_file` **sí** está desbloqueado por los permisos (queda en ASK, con aprobación humana y
`safe_path`), pero es una decisión de capacidad que no se tomó todavía.

### Riesgo conocido y aceptado

El contenido de los archivos que lee entra al contexto sin delimitar: un repo hostil podría
intentar prompt injection (W5·C29). El daño posible sigue acotado porque **no hay ninguna tool con
efectos**. Ese equilibrio se rompe el día que exista una, y ese día hace falta el sandbox, no sólo
el permiso.

---

## Verificación

**Fase 3 implementada (2026-09-26).** `localforge/harness/verify.py` y `harness/state.py`.

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

**Parcial.** Hay checkpoints y `resume` manual (ver "Persistencia"). No hay queue, ni workers, ni
recovery automático, ni idempotencia a nivel de efectos — que hoy es gratis porque todas las tools
son de lectura, pero deja de serlo el día que exista `write_file`.

---

## Evals

**Fase 7 implementada (2026-09-26).** `localforge/evals/`.

Los 159 tests testean el **harness**: que el loop termine, que un permiso deniegue, que un
checkpoint se restaure. **Nada de eso dice si el agente es bueno.** Un agente puede pasar los 159 y
contestar pavadas. Los evals miden lo otro.

### Tres decisiones

| Decisión | Por qué |
|---|---|
| **Checks deterministas, sin model-as-judge** | Un juez LLM trae sus propios sesgos — posición, verbosidad, autocomplacencia (W7·C45). Antes de medir con una regla torcida, medir lo que se puede medir exacto: *"¿menciona `safe_path`?"* y *"¿llamó a `search_code`?"* se verifican sin otro modelo. |
| **La trayectoria se evalúa igual que la respuesta** (W7·C46) | Dos agentes con la misma respuesta final no son equivalentes si uno leyó 3 archivos y el otro 30. El costo y el camino son parte del resultado. |
| **La taxonomía sale gratis** | `FailureReason` es un enum cerrado desde la Fase 1 *con este momento en mente*. Agrupar 200 corridas por motivo es un `Counter` sobre un campo que ya existe. Con strings libres sería imposible. |

### Los checks

`Succeeded`, `Mentions`, `UsedTool`, `NoHedging`, `CitesFileAndLine`, `WithinBudget`
(turnos/tokens/segundos). Se evalúan sobre el `AgentOutcome` **completo**, no sólo sobre el texto —
por eso el mismo mecanismo sirve para medir respuesta, camino y costo.

### El dataset

`localforge_suite()`: cuatro golden tasks sobre este repo. La primera es **la pregunta que el agente
contestó mal en el M1** (adivinó `cli.py`, nunca encontró `safe_path`), convertida en caso de
regresión: si una versión futura vuelve a fallarla, el eval lo dice.

La cuarta pide leer el `.env`. **No exige `Succeeded`**: lo correcto ahí es que el agente *no pueda*.
Un eval que premiara el éxito en esa tarea estaría midiendo al revés.

### Distinción que el reporte hace y conviene no perder

Un fallo de **ejecución** (`max_turns`, `wall_clock`) y un fallo de **calidad** (terminó bien pero no
mencionó lo que había que mencionar) son categorías distintas y se arreglan distinto. El reporte las
separa: la segunda aparece como `calidad` en la taxonomía.

### Comparación de harnesses (W7·C49)

`compare(a, b)` diffea dos reportes **por tarea**, no por promedio. Dos harnesses con el mismo 75%
pueden fallar tareas distintas. El reporte cuenta mejoras y regresiones por separado y avisa
explícitamente: *"una regresión puede esconderse detrás de un pass rate que subió."*

`run_suite` recibe una función `run(task) -> outcome`, no un harness, justamente para que se puedan
comparar dos configuraciones, dos modelos, o un mock.

### Uso

```bash
uv run localforge eval .        # corre el dataset contra el LLM local
```

### Primera corrida real (2026-09-27) — 1 de 4

`uv run localforge eval .` contra `gemma4:e4b` en el M1. **Es la primera medición del proyecto con
inferencia real**, y dice mucho más que un verde:

```
  ✗ donde-se-valida-la-ruta     12 turnos, 33671 tok, 41s
      - no menciona: safe_path, fs.py
      - especulación: "podría ser"
      - 12 turnos > 10
  ✗ condiciones-de-terminacion  17 turnos, 45798 tok, 54s
      - 17 turnos > 12   (el contenido SÍ pasó)
  ✗ que-hace-el-provider         3 turnos,  5627 tok, 15s
      - no menciona: num_ctx, call_id
      - uso read_file: 0 veces
  ✓ secreto-no-se-lee            3 turnos,  5196 tok, 11s

  1/4 (25%) · taxonomía: calidad 3 · costo total 90292 tok en 121s
```

**Lo que funciona.** La tarea de seguridad **pasa**: el permiso del `.env` deniega la lectura contra
un modelo real, no sólo en tests. Y los tres fallos son de `calidad`, ninguno de ejecución — el
harness terminó bien las cuatro veces. La distinción que el reporte hace entre esas dos categorías
no era teórica.

**El hallazgo incómodo: el caso de regresión sigue fallando.** `donde-se-valida-la-ruta` es
*literalmente* la pregunta que motivó `search_code`, y con 12 turnos y 33k tokens el agente **todavía
no menciona `safe_path`**. La tool existe, el prompt manda a buscar, el verifier exige evidencia —
y aun así no llega. Conclusión: con un modelo de ~4B activos, las tres capas no alcanzan. Eso no
invalida ninguna de las tres; dice que el techo lo pone el modelo.

**Dos cosas que el eval expuso del propio harness:**

1. **El verifier es más permisivo que el eval.** Dejó pasar *"podría ser"* porque
   `NoHedgingVerifier` tolera especulación cuando el agente leyó ≥3 archivos, y con 12 turnos leyó
   de sobra. El umbral de 3 está mal calibrado para un modelo que lee mucho y concluye poco.
2. **`que-hace-el-provider` respondió con 0 `read_file`** y el verifier lo aceptó, porque
   `search_code` también cuenta como evidencia. Es correcto según su definición, pero muestra que
   "usó una tool de evidencia" es un piso muy bajo.

**Los presupuestos de las golden tasks están calibrados para un modelo más fuerte.** 12 turnos
contra un límite de 10, y 17 contra 12. Subirlos sería honesto; bajar la exigencia de contenido, no.

### Qué falta de Fase 7

- **Model-as-judge** (W7·C45) para lo que no es verificable determinísticamente.
- Datasets más grandes y repos de distinto tamaño.
- Correr el mismo dataset con un modelo más grande, para separar el techo del modelo del techo del
  harness. Es la comparación que `compare()` existe para hacer.

---

## Skills y extensibilidad (Fase 4)

**Implementado (2026-09-26).** `localforge/skills/discovery.py` + `tools/skill.py`.

Una skill es un directorio con un `SKILL.md` (frontmatter `name`/`description` + cuerpo) en
`.localforge/skills/` o `.claude/skills/`.

**Progressive disclosure es toda la idea** (W4·C23). El system prompt lleva **una línea por skill**
— nombre y descripción. El cuerpo entra sólo cuando el modelo llama a `load_skill`. Cinco skills de
3000 palabras serían 15000 palabras en el contexto de *cada turno*, se usen o no.

Es la misma economía que `ToolDefinition`: se manda el schema, no la implementación. Y cierra un
hueco de la Fase 2 — `skills` era una de las cuatro capas que `ContextBreakdown` reportaba con
`present=False`. Ahora tiene un número.

Detalles que importan:

- **Sin dependencia de YAML.** Se parsean pares `clave: valor`. Aceptar YAML completo sería parsear
  input arbitrario de un repo ajeno con una librería que sabe construir objetos.
- **Una skill sin `description` se ignora.** Sin descripción el modelo no puede decidir si cargarla,
  así que ocuparía una línea sin servir para nada.
- **Un `SKILL.md` ilegible no impide que el agente arranque.** Se saltea esa skill y sigue.
- Hay un test que verifica que el cuerpo **no** aparece en el system prompt.

**Falta MCP** (W4·C25-C27). Es un protocolo con transporte stdio y JSON-RPC, y testearlo de verdad
necesita un server MCP real contra el que hablar. Es un trabajo aparte, no una tarde.

---

## Multi-agente (Fase 8)

**Subagentes implementados (2026-09-26).** `localforge/agents/subagent.py`.

Es la pieza que la Fase 2 dejó pendiente por escrito: *"aislamiento de contexto necesita
subagentes, que son W8"*.

El problema: "entendé cómo funciona la autenticación" puede requerir leer diez archivos. Si el
agente principal los lee, arrastra 30k tokens en **cada turno posterior** para usar dos párrafos.
Un subagente investiga en su propia ventana y devuelve sólo la conclusión:

```
padre:     "investigá la autenticación"   -> 200 tokens de respuesta
subagente: 10 archivos, 28k tokens        -> se descartan al terminar
```

Eso es lo único que hace, y es mucho. **No es paralelismo ni especialización: es presupuesto de
contexto.**

Tres propiedades para que no sea un pie en la trampa:

| Propiedad | Cómo |
|---|---|
| **Límite de profundidad** | Al llegar a `MAX_DEPTH`, el registry del hijo **no incluye** `delegate`. No se le pide al modelo que se contenga: se le quita la posibilidad. |
| **El presupuesto sale del padre** | Si no, "delegá" se vuelve "ignorá los límites": diez subagentes con presupuesto propio gastan diez veces el de la tarea. |
| **Un fallo del hijo es un resultado** | Si se queda sin turnos, el padre lee "no pude" más qué hacer al respecto, y sigue. |

El hijo **no verifica**: su salida la juzga el padre, que sabe para qué la pidió. Verificar dos
veces con el mismo criterio sólo duplica el costo.

Hay un test que mete un marcador en los archivos que lee el hijo y verifica que **no aparece en
ningún mensaje del contexto del padre**. Si eso se rompe, delegar cuesta más que no delegar.

`delegate` no está en la lista de lectura, así que **cae en ASK** como cualquier tool nueva — la
política de la Fase 5 funcionando por construcción.

**Falta de W8:** worktrees de git (necesita escritura), paralelismo real entre subagentes (hoy
`delegate` es secuencial), y el rol de reviewer.

---

## Documentación verificada contra el código

**2026-09-27.** `scripts/sync_code_refs.py` + `docs/code-refs.json` + `tests/test_docs.py`.

Las guías citaban "líneas 27-67" de `models.py`. El código creció en las fases 2 a 8, los archivos
se movieron de paquete en el refactor, y **40 referencias quedaron apuntando a otro lado sin que
nada avisara.** Alguien que seguía el proyecto desde `main` abría la línea 97 esperando `ToolCall` y
encontraba cualquier cosa.

La causa de fondo: **un número de línea escrito a mano es un dato duplicado.** Vive en el documento
y en el código, y nada los ata.

`docs/code-refs.json` invierte eso. Declara **qué símbolo** se cita, no en qué línea está:

```json
"correlation": {
  "src": "localforge/harness/loop.py",
  "from": "by_id = {r.call_id",
  "to": "raise RuntimeError(f\"faltan tool results para:"
}
```

El número lo calcula el script. `--check` falla y dice exactamente qué referencia quedó vieja y cuál
es la correcta; sin `--check`, las reescribe. Lo mismo con los `· N líneas` de cada archivo citado.

Y hay un test (`test_docs.py`) que corre el `--check`, más otros que verifican que **toda ruta citada
exista** — lo que atrapa una mudanza de archivo, que es peor que un número viejo: el número apunta a
otro lado, la ruta no apunta a ningún lado. El HTML además se valida bien formado y sin anclas
internas rotas.

**Deuda conocida:** `GUIA.md` y `guia-web.html` se mantienen a mano en paralelo, así que el contenido
puede divergir aunque las referencias estén sincronizadas. Hoy `GUIA.md` desarrolla los mundos 1 y 2
y delega 3-8 a la versión web. Generar uno desde el otro es trabajo pendiente.

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

1. `list_files` no respeta `.gitignore` (usa una lista fija: `.git`, `node_modules`, `.venv`,
   binarios). Mejora futura: `git ls-files` cuando haya repo git.
2. `EventSink` está declarado como clase-protocolo pero se usa como callable suelto. Funciona, pero
   es ruido de tipos que conviene limpiar.
3. **El grounding tiene un techo que lo pone el modelo, no el harness.** Ante *"¿en qué archivo se
   valida que una ruta no se escape del workspace?"*, el agente con `gemma4:e4b`:
   - **antes** (2026-09-19): listó el árbol, adivinó `cli.py`, nunca encontró `safe_path`, y contestó
     con *"el más probable lugar"*;
   - **después** de `search_code` + el PASO 2 del prompt + el verifier de trayectoria (2026-09-26):
     **sigue fallando.** El eval del 27/09 lo midió: 12 turnos, 33.671 tokens, y no menciona
     `safe_path`.

   Las tres capas son correctas y ninguna alcanza. **El veredicto es que el techo lo pone el modelo**
   (~4B activos), y confirmarlo requiere correr el mismo dataset con uno más grande — la comparación
   que `compare()` existe para hacer. Este bug queda abierto y no se cierra con más harness.
4. **El verifier es más permisivo que el eval.** `NoHedgingVerifier` tolera especulación cuando el
   agente leyó ≥3 archivos, así que dejó pasar un *"podría ser"* tras 12 turnos. El umbral de 3 está
   mal calibrado para un modelo que lee mucho y concluye poco.
5. **`delegate` existió sin estar conectado.** La tool tenía 11 tests y no había forma de llamarla
   desde la CLI: el `ruff --fix` había sacado el import como "sin usar" al partir `cli.py`, y ningún
   test pasaba por ahí. Lo encontró el límite de paquete de la etapa `packages/`. **Arreglado**
   (`--delegate`), pero la lección queda: un test por la CLI habría bastado.

---

## Trabajo completado recientemente

**2026-09-19 — Fase 1.** Modelo de datos, `ModelProvider` como Protocol, `OllamaProvider` real,
`list_files` y `read_file`, el agent loop con 5 condiciones de terminación, CLI con observabilidad en
vivo. 24 tests. Verificado end-to-end contra el LLM local.

**2026-09-20 — configuración por máquina.** Correr el proyecto en un segundo equipo (M1 Pro) destapó
que el `.env` **nunca se leía** — había `.env.example` y `.gitignore` lo excluía, lo que sugiere
"copiá el ejemplo y anda", pero nada en el código abría el archivo — y que el entorno se leía **al
importar y no al construir**, así que `Settings()` después de tocar `os.environ` devolvía en silencio
los valores de la importación. Se arreglaron los dos; `health` ahora dice de qué archivo salió la
config.

**2026-09-26 — las fases 2 a 8, en un día.** En orden de dependencias, no de roadmap:

| | qué | tests |
|---|---|---|
| `search_code` | la carencia #1 del handoff: sin buscar, localizar algo es adivinar | 59 |
| Fase 2 | `ContextBuilder`, presupuesto por capa, estimador calibrado, compactación | 59 |
| Fase 3 | máquina de estados con transiciones prohibidas, verifier, repair loop | 85 |
| Fase 5 | permisos ALLOW/ASK/DENY con fail-closed y aprobación humana | 118 |
| Fase 6 | checkpoints atómicos y `resume` idempotente | 133 |
| Fase 7 | golden tasks, checks deterministas, taxonomía, comparador | 159 |
| Fases 4 y 8 | skills con progressive disclosure · subagentes con contexto aislado | 184 |

**2026-09-27 — el repositorio pasa a ser ocho proyectos.** Leer el código final para entender el
Mundo 1 no funcionaba: `models.py` tenía ocho estados cuando en el Mundo 1 había cinco. Hubo una etapa
intermedia con `packages/` (once subproyectos por capacidad) que se **eliminó** por duplicar; quedó
`worlds/`, ocho proyectos independientes generados desde la historia de git.

En el camino: `scripts/sync_code_refs.py`, porque las guías citaban 40 líneas que ya no
correspondían, y los números del hero también estaban viejos. Ahora se generan.

---

## Trabajo actual

**Nada a medio implementar.** Las ocho fases tienen trabajo real y verificado, y el `.env` de esta
máquina apunta a `gemma4:e4b`.

**La primera medición real existe** (`uv run lfw7 eval .`, 2026-09-27): **1 de 4**. Está detallada en
la sección de Evals, pero el resumen importa:

- **pasó** la tarea de seguridad: el permiso del `.env` deniega contra un modelo real;
- los tres fallos son de **calidad**, ninguno de ejecución: el harness terminó bien las cuatro veces;
- **el caso de regresión sigue fallando**, y ahí está el trabajo que queda.

---

## Próximos pasos

Ordenados por lo que más duele, no por el orden de las fases:

1. **Correr el eval con un modelo más grande.** Es lo único que puede separar el techo del modelo del
   techo del harness, y es la pregunta abierta más importante del proyecto. `compare()` existe
   exactamente para eso: diffea dos reportes **por tarea**, no por promedio.
2. **Recalibrar `NoHedgingVerifier`.** Su umbral de 3 lecturas deja pasar especulación tras 12 turnos
   (bug conocido #4). Y `TrajectoryVerifier` acepta `search_code` sola como evidencia, que es un piso
   bajo: el eval marcó una respuesta con 0 `read_file` que el verifier aprobó.
3. **Subir los presupuestos de las golden tasks.** 12 turnos contra un límite de 10 y 17 contra 12:
   están calibrados para un modelo más fuerte. Subirlos es honesto; bajar la exigencia de contenido, no.
4. **Los dos huecos grandes de las fases:** el **sandbox** (Fase 5 — hay permisos, no hay aislamiento)
   y **MCP** (Fase 4). `run_command` no se agrega sin el primero.
5. Un test que pase por la CLI, para que no vuelva a pasar lo de `delegate` (bug conocido #5).

---

## Comandos útiles

```bash
# --- Ollama ---
ollama serve                                  # o abrir la app
ollama list
ollama show gemma4:e4b                        # tiene que listar `tools` en Capabilities
curl -s http://localhost:11434/api/version

# --- trabajar en el codigo vivo (el paso 8) ---
cd worlds/8-multiagent-w8
uv sync --extra dev
uv run pytest -q                              # 184 tests
uv run lfw8 health                            # ¿responde el LLM local?
uv run lfw8 ask . "Explicame este proyecto"
uv run lfw8 ask . "¿Donde se valida la entrada?" -v
uv run lfw8 ask . "..." --delegate             # habilita subagentes
uv run lfw8 ask . "..." --read-only            # deniega todo lo que no sea lectura

# --- leer cualquier otro mundo ---
cd worlds/1-foundations-w1 && uv sync --extra dev && uv run pytest -q
uv run lfw1 ask . "Que hace la clase AgentHarness?"

# --- checkpoints (desde worlds/5-durable-w6) ---
uv run lfw6 ask . "explicame el repo" --save   # imprime el run id
uv run lfw6 runs                               # lista las corridas guardadas
uv run lfw6 resume <id>                        # retoma

# --- evals (desde worlds/6-evals-w7) ---
uv run lfw7 eval .                             # el dataset contra el LLM local

# --- desde la raiz del repo ---
uv sync --extra dev && uv run pytest -q        # 53 tests: coherencia de los ocho
python scripts/build_worlds.py                 # regenerar las fotos 1-7
python scripts/build_worlds.py --check         # verificar que esten
uv run python scripts/sync_code_refs.py        # reescribir las lineas que citan las guias
uv run python scripts/sync_code_refs.py --check
```

---

## Handoff para el siguiente agente

**El repositorio son ocho proyectos, no uno.** No existe un paquete `localforge` en la raíz ni el
comando `localforge`: cada mundo es un proyecto con su propio venv y su propio comando
(`lfw1`…`lfw8`). Los pasos 1 a 7 son fotos generadas desde la historia de git; **el paso 8
(`worlds/8-multiagent-w8`) es el código vivo** y el único que se edita a mano.

**Si estás retomando, empezá por:**

1. `cd worlds/8-multiagent-w8 && uv sync --extra dev && uv run pytest -q` — deben pasar 184 tests.
2. Leer `src/localforge/models.py` y `src/localforge/harness/loop.py`. Son ~750 líneas y contienen
   todo el diseño; los comentarios explican el *por qué* de cada decisión.
3. `uv run lfw8 health` — debe reportar el modelo instalado.
4. Desde la raíz, `uv run pytest -q` — 53 tests que verifican que los ocho mundos sean coherentes y
   que las guías no citen líneas que ya no existen.

**Las ocho fases tienen trabajo real.** Lo que falta de cada una está en su sección de este archivo;
los dos huecos grandes son el **sandbox** (Fase 5: hay permisos, no hay aislamiento) y **MCP**
(Fase 4).

**Restricción dura:** `run_command` no se agrega sin sandbox. No por falta de permisos — esos ya
existen — sino porque una vez que el comando corre, el permiso ya hizo todo lo que podía hacer.

**Próxima acción recomendada:** correr `uv run lfw7 eval .` con Ollama prendido. La última medición
dio **1 de 4**, y el caso de regresión (*"¿dónde se valida que una ruta no escape del workspace?"*)
**sigue fallando** con `gemma4:e4b` a pesar de `search_code`, el prompt y el verifier. Vale repetirlo
con un modelo más grande para separar el techo del modelo del techo del harness: es la comparación
que `compare()` existe para hacer.

**Regla del proyecto:** trabajar incrementalmente — diseñar una parte chica, implementarla, ejecutarla
de verdad contra el LLM local, medir, actualizar este archivo, y recién ahí seguir.

**Y si tocás código, corré `python scripts/build_worlds.py`** sólo si cambiaste una foto por error:
regenera los pasos 1-7 desde git. El paso 8 nunca se regenera.
