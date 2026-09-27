# LocalForge

Coding agent que corre sobre un **LLM local** (Ollama), con un **agent harness propio y explícito**.

Sin LangChain, sin CrewAI, sin frameworks que escondan el loop. Las librerías que se usan son
infraestructura (`httpx`, `pydantic`), no abstracciones de agentes.

## El repositorio es ocho proyectos

No hay un codebase único: hay **ocho proyectos Python independientes**, uno por mundo del roadmap.
Cada uno tiene el código tal cual estaba al cerrar ese mundo, con su propio venv, sus propios tests
y su propio comando.

La razón es poder leer uno sin que se filtren los siguientes. Abrís
`worlds/1-foundations-w1/src/localforge/models.py` y `AgentStatus` tiene **cinco** estados, no ocho:
los otros tres los agregó el Mundo 3, y en el Mundo 1 no tenían sentido porque todavía no había nada
que verificara.

| paso | mundo | qué propiedad agrega | tests |
|---|---|---|---|
| [1](worlds/1-foundations-w1/) | **W1** Agent Foundations | el agente **corre y termina** | 24 |
| [2](worlds/2-context-w2/) | **W2** Context Engineering | y es **barato y preciso** | 59 |
| [3](worlds/3-harness-w3/) | **W3** Harness Engineering | y está **estructurado y verifica** | 85 |
| [4](worlds/4-sandbox-w5/) | **W5** Sandbox Engineering | y es **seguro** | 118 |
| [5](worlds/5-durable-w6/) | **W6** Durable Agents | y **sobrevive a un crash** | 133 |
| [6](worlds/6-evals-w7/) | **W7** Agent Evals | y sabés **si es bueno** | 159 |
| [7](worlds/7-skills-w4/) | **W4** Skills & Protocols | y es **extensible** | 177 |
| [8](worlds/8-multiagent-w8/) | **W8** Coding Agents & Multi-Agent | y **delega** sin pagar el contexto | 184 |

El orden es el de construcción, no el de los números: el Mundo 5 (permisos) va antes del 4 (skills)
porque los permisos eran prerequisito duro de cualquier herramienta con efectos. Así cada paso es el
anterior **más una cosa**. Detalle en [`worlds/README.md`](worlds/README.md).

**Los pasos 1 a 7 son fotos** generadas desde la historia de git; **el paso 8 es el código vivo**,
donde se sigue trabajando.

## Por dónde empezar

| Si sos… | Leé |
|---|---|
| 🧑‍🎓 **una persona aprendiendo** | [`worlds/1-foundations-w1/`](worlds/1-foundations-w1/) y de ahí en adelante |
| 📖 **…y querés el *por qué*** | **https://localforge-guia.vercel.app** — la guía, con preguntas antes de las respuestas |
| 🤖 **un agente retomando el trabajo** | [`PROJECT_STATE.md`](PROJECT_STATE.md) — estado real, decisiones y handoff |

### Proyecto hermano: AgentForge Academy

👉 **https://agentforge-academy-chi.vercel.app**

La academia interactiva con los conceptos que este proyecto implementa: 8 mundos, 56 clases, del
agent loop a los evals y los sistemas multi-agente. LocalForge es esa teoría hecha código.

## Correr cualquier mundo

```bash
cd worlds/8-multiagent-w8        # o cualquiera de los ocho
uv sync --extra dev
uv run pytest -q
uv run lfw8 health              # ¿responde el LLM local?
uv run lfw8 ask . "Explicame este proyecto"
```

Requiere Ollama en `localhost:11434` y un modelo con soporte de tool calling
(`ollama show <modelo>` tiene que listar `tools` en *Capabilities*). Python ≥ 3.12, gestionado con `uv`.

## Configuración

Todo por variables de entorno. Precedencia: **shell > `.env` > default del código**.

```bash
cp .env.example .env    # y ajustalo a tu maquina
```

Los defaults del código son neutrales: lo que depende del hardware (qué modelo tenés, cuántos turnos
y cuánto tiempo necesita) vive en el `.env`, que no se commitea. Del `.env` sólo se leen las claves
con prefijo `LOCALFORGE_` — el agente corre *sobre* otros repositorios, y esos repos tienen su propio
`.env` con secretos ajenos.

| Variable | Default | Qué controla |
|---|---|---|
| `LOCALFORGE_MODEL` | `qwen3:14b` | Modelo local |
| `LOCALFORGE_NUM_CTX` | `32768` | Ventana de contexto pedida a Ollama |
| `LOCALFORGE_MAX_TURNS` | `20` | Límite de turnos del loop |
| `LOCALFORGE_WALL_CLOCK_S` | `300` | Presupuesto de tiempo por task |

## Los tests del repositorio

Además de los tests de cada mundo, la raíz tiene los que verifican que la colección sea coherente:

```bash
uv sync --extra dev && uv run pytest -q     # 53 tests
```

Que cada módulo aparezca en su paso y **no antes**, que los tests crezcan paso a paso, que cada
carpeta sea un proyecto de verdad, y que las guías no citen líneas que ya no existen.
