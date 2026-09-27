# Los ocho mundos, uno por proyecto

Cada carpeta es un **proyecto Python completo e independiente**: su propio `pyproject.toml`, su
propio venv, su propio lockfile, su propio entrypoint y sus propios tests.

El punto es que puedas leer un mundo **sin que se filtren los siguientes**. Si abrís
`1-foundations-w1/src/localforge/models.py` vas a encontrar cinco estados, no ocho — los otros tres
todavía no existían.

| paso | mundo | qué agrega | archivos | tests |
|---|---|---|---|---|
| [1](1-foundations-w1/) | **W1** Python Agent Foundations | el agente corre y termina | 15 | 24 |
| [2](2-context-w2/) | **W2** Context Engineering | y es barato y preciso | 19 | 59 |
| [3](3-harness-w3/) | **W3** Harness Engineering | y está estructurado y verifica | 22 | 85 |
| [4](4-sandbox-w5/) | **W5** Sandbox Engineering | y es seguro | 24 | 118 |
| [5](5-durable-w6/) | **W6** Durable Agents | y sobrevive a un crash | 26 | 133 |
| [6](6-evals-w7/) | **W7** Agent Evals | y sabés si es bueno | 28 | 159 |
| [7](7-skills-w4/) | **W4** Skills & Protocols | y es extensible | 31 | 177 |
| [8](8-multiagent-w8/) | **W8** Coding Agents & Multi-Agent | y delega sin pagar el contexto | 33 | 184 |

## Correr cualquiera

```bash
cd worlds/3-harness-w3
uv sync --extra dev
uv run pytest -q                 # los 85 tests que existían en ese punto
uv run lfw3 ask . "explicame este proyecto"
```

Cada mundo tiene su propio comando (`lfw1`, `lfw2`, …) para que puedas tener varios instalados sin
que se pisen.

## Por qué el orden no es el del roadmap

Los pasos van en el orden en que el proyecto **se construyó**, y eso no es el orden de los números:
el **Mundo 5** (permisos) se hizo antes del **Mundo 4** (skills), porque los permisos eran
prerequisito duro de cualquier herramienta con efectos — la regla del proyecto era no agregar
`write_file` ni `run_command` sin ALLOW/ASK/DENY.

Ordenarlos por número de roadmap rompería la propiedad que los hace útiles: el paso 4 tendría skills
y el paso 5 las perdería. Así, **cada paso es el anterior más una cosa**, y los tests lo confirman —
crecen 24 → 59 → 85 → 118 → 133 → 159 → 177 → 184 sin que ninguno se rompa.

## Qué es una foto y qué es el código vivo

Estas ocho carpetas son **fotos para leer**, generadas desde la historia de git por
[`scripts/build_worlds.py`](../scripts/build_worlds.py). No se editan a mano: se regeneran.

El **código vivo** del proyecto está en [`packages/`](../packages/), organizado como un workspace de
once subproyectos por capacidad. Ahí es donde se trabaja; acá es donde se aprende.

```bash
python scripts/build_worlds.py          # regenerar las ocho
python scripts/build_worlds.py --check  # verificar que estén
```
