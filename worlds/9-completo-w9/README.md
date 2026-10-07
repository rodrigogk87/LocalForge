# Paso 9 — Los ocho mundos, completos

> **Y cada mundo termina lo que dejó pendiente.**

Este directorio es un **proyecto Python completo e independiente**, y es el **código vivo** del
proyecto: el único paso que se edita a mano.

Arranca como copia del [paso 8](../8-multiagent-w8/) y agrega, mundo por mundo, lo que la tabla
*"Qué le falta a LocalForge"* marcaba como hueco:

| mundo | faltaba | ahora | dónde |
|---|---|---|---|
| **W2** Context | retrieval | `retrieve`: los fragmentos más relevantes, por función, con BM25 y sin secretos en el índice | `retrieval.py`, `tools/retrieve.py` |
| **W3** Harness | planner | `--plan`: un plan sin tools antes de empezar; estado `PLANNING` | `harness/planner.py` |
| **W4** Skills & Protocols | MCP | `--mcp config.json`: cliente MCP por stdio, sin SDK | `mcp.py` |
| **W5** Sandbox | el sandbox | `--sandbox`: `run_command` en Docker sin red, repo de solo lectura, fail-closed | `sandbox.py`, `tools/command.py` |
| **W6** Durable | queue y memoria | `submit` / `worker` / `jobs` con recovery por lease; `--memory` entre sesiones | `harness/queue.py`, `harness/memory.py` |
| **W7** Evals | model-as-judge | `eval --judge`: juez con rúbrica, orden invertido contra el sesgo de posición | `judge.py` |
| **W8** Multi-Agent | worktrees | `--edit`: `delegate_edit` edita en un git worktree y devuelve un diff | `harness/worktree.py`, `tools/write.py` |

Todo es **opt-in**: sin flags, `lfw9 ask` se comporta igual que el paso 8, más la tool `retrieve`.

## Correrlo

```bash
uv sync --extra dev
uv run pytest -q
uv run lfw9 health
uv run lfw9 ask . "¿dónde se valida que una ruta no escape del workspace?" --plan --memory
```

Desde la raíz del repo, lo mismo con el Makefile: `make ask WORLD=9 Q="..." FLAGS="--plan --memory"`.

### Cada pieza, por separado

```bash
uv run lfw9 ask . "..." --plan                       # W3: plan antes de empezar
uv run lfw9 ask . "..." --memory                     # W6: recuerda corridas verificadas de este repo
uv run lfw9 ask . "corré los tests" --sandbox        # W5: necesita Docker y la imagen (no se descarga sola)
uv run lfw9 ask . "cuánto es 2+40" --mcp examples/mcp.json   # W4: un server MCP de ejemplo
uv run lfw9 ask . "agregá un docstring a main" --edit        # W8: necesita un repo git con commits
uv run lfw9 eval . --judge                           # W7: LOCALFORGE_JUDGE_MODEL para usar otro modelo

uv run lfw9 submit . "explicame el loop"             # W6: encola
uv run lfw9 worker --once                            #     un worker la toma (y recupera abandonadas)
uv run lfw9 jobs                                     #     estado de la cola
```

Necesitás Ollama con un modelo que soporte tool calling (`ollama show <modelo>` tiene que listar
`tools` en *Capabilities*). Docker solo para `--sandbox`: sin Docker, `run_command` no se habilita.

## Lo que sigue sin estar

Ninguna de estas piezas está "terminada" en el sentido de un producto. Cada una es la versión mínima
que hace honesta la casilla, con sus límites escritos en el docstring del módulo:

- **retrieval** es léxico (BM25), no semántico: sin modelo de embeddings, a propósito.
- **el sandbox** es un contenedor, no una VM: el kernel es el del host.
- **la queue** es durable por turno, no por llamada: no es Temporal.
- **MCP** cubre tools por stdio; no resources, prompts ni transporte HTTP.
- **worktrees** parten de `HEAD`: los cambios sin commitear no están en la copia.

---

*Este paso no se regenera: es el código vivo. Los pasos 1 a 7 son fotos generadas desde la historia
de git con [`scripts/build_worlds.py`](../../scripts/build_worlds.py), y el 8 es la foto congelada de
los ocho mundos tal como se cerraron.*

← [paso 8: Coding Agents & Multi-Agent](../8-multiagent-w8/)
