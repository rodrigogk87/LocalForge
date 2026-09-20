# Guía para leer LocalForge

Esto no es documentación de referencia. Es un **recorrido guiado** para que entiendas cómo
funciona un agent harness leyendo el código de a poco, en el orden correcto.

Son 5 sesiones de 30-40 minutos. No hace falta hacerlas seguidas.

**Cómo usar esta guía:** cada sección te dice qué archivo abrir y en qué línea mirar, te hace una
pregunta, y recién después te da la respuesta. **Intentá contestar antes de seguir leyendo.** Si
la respuesta te sorprende, ahí aprendiste algo.

---

## Cómo se conecta con AgentForge Academy

### 👉 **https://agentforge-academy-chi.vercel.app**

*(alias estable de producción — sobrevive a los redeploys. La URL con hash
`agentforge-academy-c5vlou4kw-orbs1.vercel.app` apunta a un deployment puntual y cambia cada vez
que se publica una versión nueva.)*

LocalForge **es** el proyecto que la Academy usa como ejemplo, construido de verdad. Cada decisión
de este código corresponde a una clase concreta del roadmap.

En esta guía hay **29 referencias cruzadas** a **59 clases** de la Academy.

Vas a ver marcas así a lo largo de la guía:

> 🎓 **W1·C5** — el concepto está explicado en el Mundo 1, Clase 5.

Las dos direcciones sirven:

- **De la Academy al código:** estudiaste el concepto, acá lo ves implementado y funcionando.
- **Del código a la Academy:** no entendés por qué algo está así, vas a la clase y te lo explica
  con diagramas y ejercicios.

### El mapa completo

| Mundo de la Academy | Qué cubre | Estado en LocalForge |
|---|---|---|
| **W1** Python Agent Foundations | Pydantic, async, Protocol, API, tool calling, agent loop | ✅ **implementado entero** |
| **W2** Context Engineering | Budgets, selección, retrieval, compactación | ⬜ medido, todavía no hace falta |
| **W3** Harness Engineering | Estados, planner, verifier, retry, hooks | ⬜ próxima fase |
| **W4** Skills & Protocols | Skills, progressive disclosure, MCP, A2A | ⬜ |
| **W5** Sandbox Engineering | Threat model, Docker, permisos, aprobación | 🟡 sólo `safe_path` |
| **W6** Durable Agents | Checkpoints, recovery, idempotencia, memoria | ⬜ nada sobrevive al proceso |
| **W7** Agent Evals | Datasets, trayectoria, costo, taxonomía de fallos | ⬜ |
| **W8** Coding Agents | Edición, worktrees, subagentes | ⬜ |

**Estás parado en el final del World 1.** Todo lo que leas en esta guía es W1 hecho código; el
resto del roadmap es lo que le falta al proyecto.

---

## Antes de empezar: el mapa mental

Todo el proyecto hace una sola cosa:

```
  vos escribís un objetivo
        ↓
  el harness se lo manda al modelo junto con la lista de herramientas
        ↓
  el modelo responde: o texto final, o "quiero usar estas herramientas"
        ↓
  si pidió herramientas: el harness las ejecuta y le devuelve los resultados
        ↓
  vuelve a preguntarle al modelo  ← y esto se repite
        ↓
  cuando el modelo responde sin pedir herramientas: terminó
```

Eso es un agente. El resto del código existe para que ese ciclo **no se descontrole**: que
termine siempre, que no gaste infinito, que valide lo que el modelo devuelve, y que un error no
mate todo.

### Cómo leer un proyecto así (el método)

No leas los archivos en orden alfabético. **Seguí el flujo de los datos.** Primero el
vocabulario (qué objetos existen), después el flujo de control (quién llama a quién), después los
bordes (dónde entran datos de afuera).

Y tres trucos que sirven siempre:

1. **Los tests son la especificación.** Si no entendés qué hace algo, buscá su test: te dice qué
   se espera que pase, con un ejemplo concreto.
2. **Los comentarios largos marcan decisiones.** En este repo los comentarios no explican *qué*
   hace la línea (eso se ve); explican *por qué está así*. Donde hay un párrafo, hay una decisión.
3. **Corré el código mientras lo leés.** Ver el valor real de una variable vale más que diez
   minutos mirándola.

---

## Sesión 0 — Verlo correr antes de leer nada (10 min)

No leas código todavía. Mirá qué hace.

```bash
cd ~/Desktop/LocalForge
uv run localforge ask . "Que hace la clase AgentHarness?"
```

Vas a ver algo así:

```
[   0.0s] ── turno 1
[   0.7s]   modelo: tool_use · 850→35 tok | 0.7s
[   0.7s]   → list_files(path='.')
[   0.8s]   ✓ list_files 564 chars
[   0.8s] ── turno 2
[   2.1s]   modelo: tool_use · 1134→117 tok | 1.4s
[   2.1s]   → read_file(path='src/localforge/harness/loop.py')
[   2.1s]   ✓ read_file 4248 chars
[   2.1s] ── turno 3
[  12.8s]   modelo: end_turn · 4975→835 tok | 10.7s
```

**Leé esa salida línea por línea:**

- `turno 1`, `turno 2`, `turno 3` — el ciclo dando vueltas. Cada turno es **una llamada al modelo**.
- `tool_use` vs `end_turn` — la señal de si el modelo quiere herramientas o ya terminó.
- `850→35 tok` — tokens que entraron / que salieron. Mirá cómo el número de entrada **crece**
  en cada turno: 850 → 1134 → 4975. Eso es la conversación acumulándose.
- `→ list_files(...)` — el modelo **pidió** una herramienta. No la ejecutó él.
- `✓ list_files 564 chars` — el harness la ejecutó y obtuvo un resultado.

**Ahora ya sabés qué buscar en el código.** Todo lo que viene explica cómo se produce esa salida.

> 🎓 **W1·C5 "Tool calling: el ciclo completo"** tiene el diagrama animado de este mismo ciclo.
> Si la salida de arriba no te cerró, mirá ese diagrama primero.

---

## Sesión 1 — `models.py`: el vocabulario (30 min)

📂 `src/localforge/models.py` (265 líneas)

Este archivo no *hace* nada: define las cosas que existen. Léelo primero porque todos los demás
archivos usan estos nombres.

> 🎓 **W1·C1 "Pydantic: el borde de confianza"** es la clase entera de este archivo. Si no
> estudiaste esa clase todavía, conviene hacerlo antes o en paralelo: explica por qué los type
> hints de Python no validan nada y por qué la salida de un LLM es entrada no confiable.

### 1.1 — Los tres enums (líneas 27-67)

```python
class AgentStatus(StrEnum):    # línea 27 — en qué estado está la tarea
class StopReason(StrEnum):     # línea 45 — por qué el modelo dejó de escribir
class FailureReason(StrEnum):  # línea 54 — por qué cortamos la ejecución
```

**Mirá `FailureReason` (línea 54).** Son 6 valores fijos: `max_turns`, `wall_clock`,
`token_budget`, `loop_detected`, `provider_error`, `cancelled`.

> **Pregunta:** ¿por qué es un enum y no un string libre donde escribir el motivo que se te ocurra?

<details>
<summary>Respuesta</summary>

Porque estos valores se van a **agrupar**. Cuando tengas 200 ejecuciones y quieras saber por qué
falla tu agente, necesitás poder contar: "el 60% de los fallos son `loop_detected`". Eso te dice
exactamente qué arreglar.

Si cada rama del código escribe un mensaje libre distinto ("se acabaron los turnos", "límite de
turnos alcanzado"), no podés agrupar nada y solo sabés que "falló".

</details>

> 🎓 **W7·C48 "Taxonomía de fallos"** — las siete categorías (model, context, tool, planning,
> execution, verification, environment) y por qué cada una tiene un arreglo distinto. Este enum es
> el primer paso hacia esa taxonomía.

### 1.2 — `ToolCall` y `ToolResult` (líneas 97 y 110)

Estos dos van **en par**. El modelo emite un `ToolCall`, el harness produce un `ToolResult`.

```python
class ToolCall(BaseModel):     # línea 97
    id: str                    # ← la clave de correlación
    name: str
    arguments: dict[str, Any]

class ToolResult(BaseModel):   # línea 110
    call_id: str               # ← apunta al id de arriba
    success: bool              # ← mirá que NO tiene default
```

> **Pregunta:** ¿por qué `success: bool` no tiene un valor por defecto como `= True`?

<details>
<summary>Respuesta</summary>

Porque un default optimista convierte **cada olvido en un falso positivo**.

Imaginate que en algún lado del código se construye un `ToolResult` apurado, sin pasar `success`.
Si el default fuera `True`, el harness le dice al modelo "la operación salió bien" y el agente
sigue construyendo sobre una premisa falsa. **Sin ningún error, sin ningún log.**

Al no tener default, Python te obliga a decidir. Es más molesto de escribir y ese es el punto.

Regla general: **en campos de resultado y de seguridad, el default seguro es el pesimista — o
directamente no hay default.**

</details>

> 🎓 **W1·C1 → ejercicio "Fix the bug: Un default que miente"** es *exactamente* este caso, con
> este mismo modelo `ToolResult`. Si lo hiciste, acabás de ver el arreglo en producción. Si no,
> andá a hacerlo ahora: es el ejercicio que más rápido te cambia el instinto sobre defaults.
>
> El mismo criterio vuelve en **W5·C34 (permisos)**: el default de un permiso desconocido nunca
> es `ALLOW`.

### 1.3 — El validador de coherencia (línea 126)

```python
@model_validator(mode="after")
def _coherent(self) -> ToolResult:
    if self.success and self.error is not None:
        raise ValueError("un resultado exitoso no puede traer error")
    if not self.success and not self.error:
        raise ValueError("un fallo debe explicar por que fallo")
```

`mode="after"` significa que corre **después** de que cada campo se validó por separado. Por eso
puede razonar sobre la relación *entre* campos, que es algo que ningún campo suelto puede expresar.

La segunda regla es la importante: **un fallo está obligado a explicar por qué falló.** No podés
tener un `ToolResult` que diga "falló" y nada más.

### 1.4 — `as_content()` (línea 134)

```python
def as_content(self) -> str:
    if self.success:
        return self.output or "(sin salida)"
    return f"ERROR: {self.error}"
```

Tres líneas, pero acá está una de las ideas centrales del proyecto.

> **Pregunta:** cuando una herramienta falla, ¿por qué el error se convierte en **texto que vuelve
> al modelo**, en vez de lanzar una excepción?

<details>
<summary>Respuesta</summary>

Tu instinto de backend dice: falla rápido y ruidoso.

Pero en un agente eso significa que **la tarea entera muere** porque el modelo probó una ruta
razonable que resultó no existir. Un humano en esa situación haría `ls` y probaría otra vez.

Con excepciones propagando, un agente de 20 pasos tiene ~20 oportunidades de morir por algo
perfectamente recuperable.

Devolviendo el error como texto, el modelo lee "no existe src/mian.py" y en el próximo turno pide
`list_files`. **Se autocorrige sin que escribas una sola línea de lógica de recuperación.**

El prefijo `ERROR:` es deliberado: le da al modelo una señal léxica clara de que eso no es un
resultado válido.

</details>

> 🎓 **W1·C5 → "El error como feedback: la idea más contraintuitiva del curso"**. Ahí está la
> regla para decidir qué SÍ debe cortar la ejecución (credenciales inválidas, presupuesto agotado,
> el mismo error 3 veces) y qué vuelve como feedback.

### 1.5 — `AgentTask` (línea 202)

```python
max_turns: int = Field(default=20, ge=1, le=100)
wall_clock_s: float = Field(default=300.0, gt=0)
token_budget: int = Field(default=200_000, gt=0)
```

Estos tres números son **lo único que separa a tu agente de un proceso que corre para siempre
gastando plata**. Volvés a verlos en la Sesión 2.

> 🎓 **W1·C1 → walkthrough de `AgentTask`, paso "max_turns: el primer límite de seguridad"**.
> Ahí se explica por qué `ge=1` y `le=100`, y por qué el `id` es un UUID y no un autoincrement.
> Es literalmente el mismo modelo que estás mirando.

### 1.6 — `AgentOutcome` (línea 229)

Lo que devuelve el agente cuando termina. Mirá todo lo que trae: `status`, `reason`, `turns`,
`input_tokens`, `output_tokens`, `duration_ms`, `trajectory`.

> **Pregunta:** ¿por qué no devolver simplemente el string con la respuesta final?

<details>
<summary>Respuesta</summary>

Porque un string **no te deja distinguir "terminó bien" de "se acabaron los turnos"**. Las dos
cosas son texto.

Y además: sin tokens no podés saber cuánto costó, sin turnos no sabés si fue eficiente, y sin
`trajectory` (la lista de herramientas que usó, en orden) no podés responder preguntas como "¿leyó
el archivo antes de editarlo?".

Todo eso es lo que más adelante permite **comparar dos versiones del harness con datos** en vez de
con impresiones.

</details>

> 🎓 **W1·C6 → challenge "AgentOutcome: el registro que hace evaluable a tu agente"**. Ese
> ejercicio te hace escribir esta misma clase desde cero. Y **W7·C47 "Costo y latencia"** explica
> por qué `input_tokens` y `output_tokens` van separados (el output cuesta ~5x más).

### ✅ Checkpoint Sesión 1

Deberías poder responder sin mirar:

1. ¿Por qué `FailureReason` es un enum cerrado?
2. ¿Por qué `success` no tiene default?
3. ¿Qué pasa cuando una herramienta falla — excepción o texto? ¿Por qué?

> 🎓 Si alguna te costó, el quiz de **W1·C1** y el de **W1·C5** cubren exactamente estas tres.

---

## Sesión 2 — `harness/loop.py`: el corazón (40 min)

📂 `src/localforge/harness/loop.py` (240 líneas)

Este es **el archivo más importante del proyecto**. Todo lo demás existe para servirlo.

> 🎓 **W1·C6 "El agent loop y sus condiciones de terminación"** es la clase de este archivo, y su
> walkthrough recorre un loop casi idéntico línea por línea. **W1·C7 (el Boss del Mundo 1)**
> integra todo esto.

### 2.1 — El estado del loop (líneas 85-97)

```python
messages: list[AgentMessage] = [...]   # la conversación
tokens_in = 0                          # cuánto gastamos
tokens_out = 0
seen: Counter[str] = Counter()         # para detectar repeticiones
trajectory: list[str] = []             # qué herramientas usó
records: list[TurnRecord] = []         # el detalle de cada turno

started = time.monotonic()
deadline = started + task.wall_clock_s
```

Seis variables locales. **No están escondidas en `self`**, y eso es a propósito.

> **Pregunta:** ¿por qué importa que el estado esté en variables locales explícitas?

<details>
<summary>Respuesta</summary>

Porque **esta tupla exacta es lo que más adelante hay que guardar en disco** para que el agente
pueda sobrevivir a un crash y retomar donde estaba.

Lo que es fácil de nombrar es fácil de serializar. Si el estado estuviera repartido en diez
atributos de la clase mezclados con conexiones HTTP y handles de archivos, guardar y restaurar
sería un problema.

Fijate también en `time.monotonic()` y no `time.time()`: `monotonic` solo avanza, nunca salta.
`time.time()` puede moverse para atrás si el sistema ajusta el reloj (NTP, cambio de horario), y
ahí tu presupuesto de tiempo se vuelve loco.

</details>

> 🎓 **W6·C36 "State persistence"** y **W6·C37 "Checkpoints"** — esta tupla es exactamente lo que
> ahí se serializa. La regla de esas clases ("datos se persisten, recursos se reconstruyen") es la
> razón de que acá no haya ninguna conexión HTTP mezclada con el estado.
>
> Lo de `monotonic` vs `time()` está en el quiz de **W1·C2**.

### 2.2 — Los presupuestos, ANTES de gastar (líneas 119-126)

```python
for turn in range(task.max_turns):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return finish(..., reason=FailureReason.WALL_CLOCK, turns=turn)
    if tokens_in + tokens_out > task.token_budget:
        return finish(..., reason=FailureReason.TOKEN_BUDGET, turns=turn)
```

El orden importa: **primero verificás, después llamás al modelo.** Al revés ya gastaste la llamada
que sabías que no podías pagar — y con contexto grande esa última llamada puede ser la más cara
de toda la ejecución.

> 🎓 Es una pregunta literal del quiz de **W1·C6**.

### 2.3 — La composición de presupuestos (línea 133)

```python
response = await asyncio.wait_for(
    self.provider.complete(messages, definitions, system=system),
    timeout=min(self.cfg.request_timeout_s, remaining),
)
```

Mirá ese `min(...)`.

> **Pregunta:** el `for turn in range(task.max_turns)` ya limita las iteraciones. ¿Por qué hace
> falta además un timeout?

<details>
<summary>Respuesta</summary>

**Porque limitar iteraciones no es lo mismo que limitar tiempo.**

Hay dos tipos de terminación y necesitás los dos:

- **Terminación lógica** — el loop hace una cantidad acotada de vueltas. La da el `for`.
- **Terminación temporal** — el loop termina en tiempo acotado. La da el `wait_for`.

Sin la segunda, un solo turno puede colgarse para siempre: si el servidor acepta la conexión y
nunca responde, `complete()` espera indefinidamente y **nunca llegás a la segunda iteración**. El
límite de 20 turnos es irrelevante.

En producción eso es indistinguible de un deadlock: el agente no falla, se queda quieto.

Y el `min()` es la parte fina: el turno tiene su propio límite (180s), pero **nunca puede pasarse
del presupuesto total que queda**. Si quedan 8 segundos, el turno tiene 8, no 180.

</details>

> 🎓 Dos clases se cruzan acá:
>
> - **W1·C6 → predict "¿Puede este loop no terminar?"** es exactamente esta pregunta, y la
>   respuesta distingue terminación *lógica* de *temporal*.
> - **W1·C2 → build "Budget de tiempo en tres niveles"** te hace escribir este `min(60, remaining)`
>   a mano. Si lo hiciste, acabás de encontrar tu propio ejercicio en el código real.
>
> El mismo patrón vuelve en **W5·C32**, donde se aprende que `asyncio.wait_for` **no alcanza** para
> matar un proceso dentro de un container: ahí el timeout real es destruir el container.

### 2.4 — `max_tokens` no es un final válido (líneas 162-170)

```python
if not response.tool_calls:
    records.append(record)
    if response.stop_reason is StopReason.MAX_TOKENS:
        messages.append(AgentMessage(role="assistant", content=response.content))
        messages.append(AgentMessage(role="user", content="Tu respuesta quedo cortada. Continua."))
        continue
    return finish(AgentStatus.COMPLETED, output=response.content or "", turns=turn + 1)
```

Si el modelo llegó al techo de tokens a mitad de una frase, **no hay tool calls** — así que sin
ese `if`, el código haría `return` de un texto cortado como si fuera la respuesta final.

Nadie se entera: no hay excepción, el estado queda `COMPLETED`, y el resultado es basura.
Fijate que es `continue`, no `return`: consume un turno del presupuesto, que es lo correcto.

> 🎓 **W1·C4 → fix the bug "El loop que acepta respuestas truncadas"** es este bug exacto, con el
> arreglo lado a lado. Y **W1·C4** entera explica `stop_reason` como *la señal de control del
> loop*: ignorarla es el bug nº1 de un agente casero.

### 2.5 — Detección de loops (líneas 176-187)

```python
for call in response.tool_calls:
    key = _signature(call)
    seen[key] += 1
    if seen[key] >= REPEAT_LIMIT:   # REPEAT_LIMIT = 3
        return finish(..., reason=FailureReason.LOOP_DETECTED, ...)
```

Y la firma (línea 225):

```python
def _signature(call: ToolCall) -> str:
    return f"{call.name}:{json.dumps(call.arguments, sort_keys=True, default=str)}"
```

El `sort_keys=True` es importante: sin él, `{"a":1,"b":2}` y `{"b":2,"a":1}` darían firmas
distintas siendo la misma llamada.

> **Pregunta:** ya hay límite de turnos, de tiempo y de tokens. ¿Para qué una cuarta condición?

<details>
<summary>Respuesta</summary>

Porque un modelo atascado **no viola ninguna de las otras tres**. Tiene presupuesto de sobra: está
leyendo el mismo archivo tres veces esperando que cambie.

Sin este chequeo, el agente quema los 20 turnos completos repitiéndose y recién ahí corta. Con
él, corta en 3 y te dice exactamente qué estaba repitiendo.

Por qué 3 y no 2: repetir una vez puede ser un reintento legítimo tras un error transitorio. Tres
veces idénticas significa que el modelo no está incorporando el resultado.

</details>

> 🎓 **W1·C6 → "la quinta salida, la que se olvida"**, y el quiz de esa clase tiene el caso
> diagnóstico: *"tu agente termina siempre en 20 turnos exactos"* — la firma de un loop de
> repetición. En **W3·C19** esto evoluciona: en vez de cortar, se le inyecta un mensaje para que
> cambie de estrategia (eso es *reflection*).

### 2.6 — El orden de los mensajes (líneas 192-196)

```python
messages.append(
    AgentMessage(role="assistant", content=response.content, tool_calls=response.tool_calls)
)
results = await executor.run_all(response.tool_calls)
```

El mensaje del assistant va **antes** de los resultados. Primero queda registrado que los pidió,
después que devolvieron. Es una invariante del protocolo: cada resultado necesita su pedido previo.

> 🎓 **W1·C5 → walkthrough "Un turno completo de tool calling"**, paso 3. El quiz de esa clase
> pregunta qué pasa si devolvés 2 resultados para 3 tool calls (spoiler: la API rechaza la
> request, y por eso el executor devuelve *siempre* un `ToolResult` por cada call).

### 2.7 — La línea más importante del proyecto (líneas 201-204)

```python
by_id = {r.call_id: r for r in results}
missing = {c.id for c in response.tool_calls} - set(by_id)
if missing:
    raise RuntimeError(f"faltan tool results para: {missing}")
```

> **Pregunta:** ¿por qué armar un diccionario por `call_id` en vez de simplemente recorrer las dos
> listas en paralelo con `zip(calls, results)`?

<details>
<summary>Respuesta</summary>

**Porque las herramientas se ejecutan en paralelo y el orden de finalización no es el de pedido.**

Si el modelo pide leer `main.py` (tarda 800ms) y `README.md` (tarda 50ms), el README termina
primero. Si correlacionás por posición, el modelo recibe **el contenido del README etiquetado como
main.py**.

Y acá está lo peligroso: **eso no produce ningún error**. Los dos son strings válidos. El modelo
razona impecablemente sobre datos que tu harness corrompió, y el síntoma se ve como "el modelo
alucina".

Es el bug que se diagnostica mal casi siempre: se cambia el prompt, se prueba otro modelo, se
sube el contexto — y el bug determinista sigue ahí.

El `raise RuntimeError` de abajo es una red de seguridad: si alguna vez falta un resultado, es un
bug **nuestro** y queremos que explote fuerte, no que pase desapercibido.

</details>

> 🎓 **Este es el concepto más repetido de toda la Academy**, y por algo:
>
> - **W1·C5 → predict "Resultados desordenados"** — el bug con el código exacto.
> - **W1·C7 (Boss) → "Diagnóstico 1: el agente que *alucina*"** — cómo se ve desde afuera y por
>   qué se diagnostica mal.
> - **W7·C48** — lo clasifica como **execution failure**, "la categoría más peligrosa porque se
>   disfraza de fallo del modelo".
> - **Modo entrevista → "Tu agente alucina: ¿por dónde empezás?"** — es pregunta de entrevista.
>
> La regla que sale de ahí: **cuando el síntoma es "el modelo alucina", sospechá primero de tu
> harness.** Lo vas a comprobar vos mismo en el Experimento 1 de la Sesión 5.

### 2.8 — El final (línea 222)

```python
return finish(AgentStatus.FAILED, reason=FailureReason.MAX_TURNS, turns=task.max_turns)
```

El `for` que termina sin `return` **es** la condición de terminación por turnos. Esta línea es la
que garantiza que, sea cual sea el camino, el loop siempre devuelve algo.

### ✅ Checkpoint Sesión 2

1. ¿Por qué el `for` solo no garantiza que el agente termine?
2. ¿Qué pasa si correlacionás resultados por posición en vez de por `call_id`?
3. ¿Por qué `max_tokens` no se trata como una respuesta final?
4. Nombrá las 5 condiciones de terminación.

> 🎓 Las cuatro están en el **Boss del Mundo 1 (W1·C7)**. Si podés contestarlas, ganás ese boss.
> La 1 y la 4 además son pregunta de entrevista: *"¿cómo garantizás que un agent loop siempre
> termine?"*

---

## Sesión 3 — `tools/base.py`: el borde de confianza (30 min)

📂 `src/localforge/tools/base.py` (194 líneas)

### 3.1 — `definitions()` (línea 61)

```python
schema = tool.args_model.model_json_schema()
```

Una sola definición produce **dos cosas**: la descripción que lee el modelo para saber qué mandar,
y el validador que parsea lo que el modelo mandó.

Si fueran dos fuentes separadas, tarde o temprano agregás un campo en una y te olvidás de la otra.
El modelo manda algo que tu validador rechaza, y el agente entra en un bucle de reintentos que
nunca puede ganar.

> 🎓 **W1·C4 → build "De modelo Pydantic a tool definition"** te hace escribir este mismo
> `model_json_schema()`. Y la sección **"Real Agent Connection"** de **W1·C1** muestra la cadena
> completa: `BaseModel → JSON Schema → tool definition → el modelo responde → model_validate()`.
> El mismo schema reaparece en **W4·C26** cuando un MCP server publica sus herramientas.

### 3.2 — `run_one`: cuatro formas de fallar (líneas 97-170)

Leé el método entero de corrido. Fijate que **los cuatro casos de error devuelven un `ToolResult`,
ninguno lanza una excepción**:

| Línea | Caso | Qué le dice al modelo |
|---|---|---|
| 104 | La herramienta no existe | "tool desconocida 'X'. Disponibles: ..." ← **le da la lista** |
| 120 | Argumentos inválidos | El campo exacto que está mal, vía `exc.errors()` |
| 137 | Timeout | "la tool supero Ns" |
| 149 | Explotó algo inesperado | `TipoDeError: mensaje` |

**El patrón:** cada error incluye *qué hacer al respecto*. Decirle qué herramientas sí existen
sube muchísimo la probabilidad de que el próximo intento sea correcto.

> 🎓 **W1·C5 → build "Validar argumentos y devolver el error como feedback"** es esta función,
> como ejercicio. Incluye por qué `except ValidationError` y no `except Exception` (atrapar todo
> haría que un bug *tuyo* se le reporte al modelo como si fuera culpa de sus argumentos, y el
> modelo intente corregir algo que no puede arreglar).
>
> Y **W4·C26** aplica la misma idea del otro lado del protocolo: un buen error de un MCP server
> no solo dice qué está mal, dice **qué herramienta usar en su lugar**.

### 3.3 — El `except Exception` (línea 149)

```python
except Exception as exc:  # noqa: BLE001 - aislar fallos de una tool
```

> **Pregunta:** ¿por qué `except Exception` y no `except BaseException`?

<details>
<summary>Respuesta</summary>

Porque `asyncio.CancelledError` **hereda de `BaseException`, no de `Exception`**.

Si escribieras `except BaseException`, atraparías las cancelaciones — y **romperías todo el
sistema de timeouts del harness** sin darte cuenta. El `wait_for` de más arriba quedaría esperando
a una corrutina que decidió ignorar el pedido de cancelar.

Ese detalle de una palabra es lo que hace que los timeouts anidados funcionen.

</details>

> 🎓 **W1·C2 → walkthrough "El ejecutor de tools paralelo del harness"**, paso 6, con la
> advertencia en rojo. El quiz de esa clase pregunta qué pasa si una tool atrapa `CancelledError`
> y no la re-lanza: *le estás diciendo al runtime que ignoraste su pedido de cancelar*.

### 3.4 — `_truncate` (línea 179)

```python
head + f"\n\n[...truncado: se muestran {self.output_limit} de {len(text)} caracteres. "
       "Usa los parametros offset/limit de la tool para leer el resto.]"
```

**Nunca truncar en silencio.** Si el modelo cree que vio el archivo entero cuando no fue así,
razona sobre información faltante sin ninguna señal de que le falta algo.

El mensaje dice qué pasó **y cómo seguir**.

> 🎓 **W1·C5** lo lista como "Fallo 4: resultado gigante — **este sí te rompe el agente**". Y es
> una de las tres causas de alucinación aparente del **Boss del Mundo 1**, junto con la
> correlación rota y la pérdida al compactar. En **W2** esto deja de ser una constante (8000) y
> pasa a ser un presupuesto calculado.

### ✅ Checkpoint Sesión 3

1. ¿Por qué el schema de argumentos se genera y no se escribe a mano?
2. ¿Qué tienen en común los 4 mensajes de error de `run_one`?
3. ¿Qué pasaría con `except BaseException`?

> 🎓 Estas tres salen de **W1·C2** y **W1·C5**.

---

## Sesión 4 — `providers/ollama.py`: la suciedad del mundo real (30 min)

📂 `src/localforge/providers/ollama.py` (209 líneas)

Este archivo existe para que **el resto del proyecto no se entere** de las rarezas de Ollama.
Es el único módulo que sabe que Ollama existe.

Antes, mirá `providers/base.py` (54 líneas): el `Protocol` tiene **un solo método**. Cada método
que agregás es un método que todo backend futuro tiene que implementar.

> 🎓 **W1·C3 "Protocol: el harness no conoce a su proveedor"** es la clase entera de este archivo:
> structural typing vs nominal (`Protocol` vs `ABC`), la regla de la interface angosta, y por qué
> `runtime_checkable` **no** valida firmas — solo comprueba que los métodos existan por nombre.
>
> Esa clase también explica por qué esto decide si tu agente es testeable: el `ScriptedProvider`
> de `tests/test_loop.py` existe gracias a este Protocol.

### 4.1 — Inventar los `call_id` (línea 83)

```python
def _next_call_id(self) -> str:
    self._call_seq += 1
    return f"call_{self._call_seq:03d}_{uuid4().hex[:6]}"
```

**Ollama no devuelve ids en las tool calls.** OpenAI y Anthropic sí.

Pero el harness correlaciona por `call_id` (Sesión 2.7). Entonces el provider los fabrica.

> **Pregunta:** ¿por qué se inventan acá y no en el loop?

<details>
<summary>Respuesta</summary>

Porque es **el borde correcto**. El harness declara la garantía que necesita ("cada tool call
tiene un id único"), y cada adapter la cumple como pueda para su backend.

Si el loop tuviera que saber "si el provider es Ollama, generá ids", ya no sería independiente del
proveedor — que es todo el punto de tener un `Protocol`.

</details>

> 🎓 **W1·C3 → "La regla de la interface angosta"**. Este es un caso que la Academy no cubre
> explícitamente porque usa APIs que sí devuelven ids: **es un hallazgo propio de LocalForge**, y
> está documentado en `PROJECT_STATE.md` → *Modelo local → Limitaciones encontradas*.

### 4.2 — `num_ctx` explícito (línea 104)

```python
options: dict[str, Any] = {"num_ctx": self.cfg.num_ctx}
```

Si el prompt excede `num_ctx`, **Ollama lo trunca por la izquierda y sin avisar** — se come el
system prompt. El agente de golpe "se olvida" de que tiene herramientas.

Por eso se fija explícito en cada request en vez de confiar en el default del modelo.

> 🎓 **W2·C12 → predict "La ventana deslizante que rompe el agente"** es el mismo fallo por otra
> vía: cuando se pierde el objetivo original, *el agente sigue pareciendo competente* — cada
> acción individual es razonable, lo que se perdió es el marco. Es el fallo más difícil de
> diagnosticar de los agentes largos.

### 4.3 — `done_reason` no alcanza (líneas 176-181)

```python
stop = _STOP_MAP.get(done_reason, StopReason.UNKNOWN)
if tool_calls:
    stop = StopReason.TOOL_USE
```

Ollama manda `done_reason: "stop"` **aunque haya pedido herramientas**. Para el loop lo que importa
es que hay trabajo pendiente, así que la señal se deriva de la *presencia* de tool calls.

> 🎓 **W1·C4 → "stop_reason: la señal que gobierna el loop"**, con la tabla de qué hace el harness
> con cada valor. Acá se ve por qué conviene escribir el adapter a mano: la Academy enseña el
> contrato ideal, y el mundo real tiene esta clase de desprolijidades en cada backend.

### ✅ Checkpoint Sesión 4

1. ¿Cuántos métodos tiene `ModelProvider` y por qué tan pocos?
2. ¿Qué tres cosas raras de Ollama quedan encapsuladas acá?

> 🎓 **W1·C3** y **W1·C4**. La pregunta 1 es de entrevista: *"¿por qué el harness recibe el
> ModelProvider por constructor en vez de importarlo?"*

---

## Sesión 5 — Rompelo (la sesión que más enseña)

Leer no alcanza. Los fallos de un agente son contraintuitivos porque **casi ninguno se ve como un
error**. La única forma de que se te graben es provocarlos.

Hacé cada experimento, corré el agente, **y volvé a dejar el código como estaba** (`git checkout .`).

| # | Experimento | Clase de la Academy |
|---|---|---|
| 1 | Romper la correlación por `call_id` | **W1·C5** · W1·C7 · W7·C48 |
| 2 | Ahogar el contexto (`num_ctx=2048`) | **W2·C9** · W2·C12 |
| 3 | Sacar el truncado | **W1·C5** · W2·C8 |
| 4 | Volver al prompt sin grounding | **W2** · W3·C18 |
| 5 | Terminación con `--max-turns 1` | **W1·C6** |

### Experimento 1 — Romper la correlación ⭐ el más importante

En `harness/loop.py`, alrededor de la línea 201, reemplazá:

```python
by_id = {r.call_id: r for r in results}
```

por:

```python
by_id = {c.id: r for c, r in zip(response.tool_calls, sorted(results, key=lambda r: r.duration_ms))}
```

Corré: `uv run localforge ask . "Compara cli.py con models.py"`

**Qué vas a ver:** el modelo describe un archivo con el contenido de otro. Sin errores. Sin logs.
Una respuesta perfectamente coherente y completamente falsa.

**Por qué importa:** esto se ve igual que "el modelo alucina". Si no conocés el bug, cambiás el
prompt, probás otro modelo, subís el contexto — y nunca lo encontrás.

> 🎓 Después de verlo, andá a **W1·C7 → "Diagnóstico 1: el agente que *alucina*"**. Vas a leer el
> mismo caso sabiendo exactamente cómo se siente. Y a **W7·C48**, que lo clasifica como *execution
> failure* y explica por qué es la categoría más peligrosa.

### Experimento 2 — Ahogar el contexto

```bash
LOCALFORGE_NUM_CTX=2048 uv run localforge ask . "Explicame el proyecto"
```

**Qué vas a ver:** el agente se comporta raro, ignora las herramientas o responde cualquier cosa.
Ollama se comió el system prompt por la izquierda y no avisó.

### Experimento 3 — Sacar el truncado

En `tools/base.py` línea 186, hacé que `_truncate` devuelva siempre `(text, False)`.

Corré el agente sobre un repo con archivos grandes y mirá cómo crece `input_tokens` por turno.

### Experimento 4 — Volver al prompt sin grounding

```bash
git show a3d4a02:src/localforge/harness/prompt.py > src/localforge/harness/prompt.py
uv run localforge ask . "Explicame la arquitectura"
```

**Qué vas a ver:** el agente responde leyendo **cero archivos**, inventando a partir de los
nombres. Compará con el prompt actual y mirá qué cambió (`git diff`).

Después: `git checkout src/localforge/harness/prompt.py`

### Experimento 5 — Terminación

```bash
uv run localforge ask . "Analiza todo el proyecto en detalle" --max-turns 1
```

**Qué vas a ver:** `failed (max_turns)` — un final **explícito y con motivo**, no un cuelgue.

---

## Qué leer después

Cuando termines las 5 sesiones, leé los **tests** — son la especificación ejecutable:

- `tests/test_loop.py` — cada test es un escenario del harness con nombre en castellano
- `tests/test_tools.py` — los bordes: traversal, paginación, errores

Y después `PROJECT_STATE.md`, que ahora te va a resultar obvio, sobre todo la sección de
**Decisiones de arquitectura**.

---

## Qué le falta a LocalForge, mundo por mundo

Esta es la otra mitad de la conexión: **el roadmap de la Academy es el backlog del proyecto.**

| Mundo | Qué le daría a LocalForge | Señal de que ya hace falta |
|---|---|---|
| **W2** Context | ContextBuilder, budgets, compactación, retrieval | `input_tokens` acercándose a `num_ctx`. Hoy: 4975 de 32768 → **todavía no** |
| **W3** Harness | Máquina de estados, verifier, repair loop, hooks | El día que el agente **escriba** código: hay que verificar que compile y que los tests pasen |
| **W4** Skills | Skills del repo (convenciones del equipo), MCP | Cuando quieras que el agente sepa *cómo se hacen las cosas acá* |
| **W5** Sandbox | ALLOW/ASK/DENY, Docker, límites | **Antes** de agregar `write_file` o `run_command`. No negociable |
| **W6** Durable | Checkpoints, recovery, idempotencia | Cuando una tarea tarde minutos y perderla duela |
| **W7** Evals | Golden tasks, trayectoria, taxonomía | Cuando quieras saber si un cambio mejoró de verdad |
| **W8** Multi-agent | Worktrees, subagentes, reviewer | Sólo con un agente individual sólido |

**La única restricción dura:** `write_file` y `run_command` **no se agregan sin los permisos del
W5**. Hoy toda la seguridad del proyecto descansa en que el agente es de solo lectura — por eso el
riesgo de prompt injection (**W5·C29**) es acotado. Ese equilibrio se rompe el día que exista una
herramienta con efectos.

### El hallazgo de grounding, y por qué es W3

En la primera corrida real el agente respondió **sin leer un solo archivo**, infiriendo de los
nombres. Lo arreglamos con el system prompt (ver `docs/dev-log/2026-09-19.md`) y pasó a leer 3.

Pero eso es una mejora **probabilística**. La garantía dura es un **verifier de trayectoria**
(**W3·C18**): rechazar la respuesta final si el agente no leyó ningún archivo.

> Es exactamente el principio de esa clase: *la afirmación del modelo no es evidencia*. Y encima
> el chequeo se escribe sobre `outcome.trajectory`, que ya existe — igual que el
> `check_read_before_write` de **W7·C46**.

## Si te trabás

Cualquier función se entiende más rápido corriéndola sola:

```bash
uv run python
```

```python
from localforge.tools import default_registry
r = default_registry()
r.names()
r.definitions()[0].input_schema      # el JSON Schema que ve el modelo

from localforge.models import ToolResult
ToolResult(call_id="x", success=False)   # mirá qué error da y por qué
```
