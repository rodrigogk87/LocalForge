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

Los comandos viven en un solo lugar, el `Makefile`. `WORLD` es el número de **paso** (1 a 8):

```bash
make                                    # la ayuda, con todos los targets
make worlds                             # los ocho pasos y su comando
make setup-all                          # instala los ocho

make test WORLD=3                       # los 85 tests del paso 3
make test-all                           # los ocho, con resumen
make health WORLD=1                     # ¿responde el LLM local?
make ask WORLD=1 Q="Que hace AgentHarness?"
make ask WORLD=8 Q="..." FLAGS="--delegate -v"
make eval WORLD=6                       # el dataset de golden tasks
```

Sin `make` funciona igual, sólo que con tres pasos en vez de uno:
`cd worlds/1-foundations-w1 && uv sync --extra dev && uv run lfw1 ask . "..."`.

Requiere Ollama en `localhost:11434`. **No hace falta que tengas un modelo en
particular:** si el configurado no está instalado, el provider busca uno que sí esté y que soporte
tool calling, y te dice cuál eligió. Python ≥ 3.12, gestionado con `uv`.

## Configuración

**Un solo `.env`, en la raíz, para los ocho mundos.**

```bash
cp .env.example .env    # y ajustalo a tu maquina
```

El `Makefile` lo incluye y lo **exporta**, así los ocho lo ven sin que tengas que repetir nada. Eso
importa más de lo que parece: el Mundo 1 no sabe leer un `.env` — esa capacidad llegó en el Mundo 2 —
y sin el export quedaba pidiendo el default del código, que es el modelo de otra máquina.

Precedencia: **shell > `.env` > autodetección**. Del `.env` sólo se leen las claves con prefijo
`LOCALFORGE_`, porque el agente corre *sobre* otros repositorios y esos repos tienen su propio `.env`
con secretos ajenos.

| Variable | Default | Qué controla |
|---|---|---|
| `LOCALFORGE_MODEL` | `qwen3:14b` | Modelo local |
| `LOCALFORGE_NUM_CTX` | `32768` | Ventana de contexto pedida a Ollama |
| `LOCALFORGE_MAX_TURNS` | `20` | Límite de turnos del loop |
| `LOCALFORGE_WALL_CLOCK_S` | `300` | Presupuesto de tiempo por task |

## Los tests del repositorio

Además de los tests de cada mundo, la raíz tiene los que verifican que la colección sea coherente:

```bash
make repo-test     # o: uv sync --extra dev && uv run pytest -q
make check         # docs-check + repo-test + test-all: todo antes de un commit
```

Que cada módulo aparezca en su paso y **no antes**, que los tests crezcan paso a paso, que cada
carpeta sea un proyecto de verdad, y que las guías no citen líneas que ya no existen.
