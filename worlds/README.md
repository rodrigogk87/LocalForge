# LocalForge, en ocho mundos

Cada carpeta es un **proyecto Python completo e independiente**: su propio `pyproject.toml`, su
propio venv, su propio lockfile, sus propios tests y su propio comando.

El punto es que puedas leer un mundo **sin que se filtren los siguientes**. Si abrís
`1-foundations-w1/src/localforge/models.py` vas a encontrar cinco estados, no ocho — los otros tres
todavía no existían.

| paso | mundo | qué agrega | archivos | tests | comando |
|---|---|---|---|---|---|
| [1](1-foundations-w1/) | **W1** Agent Foundations | el agente corre y termina | 15 | 24 | `lfw1` |
| [2](2-context-w2/) | **W2** Context Engineering | y es barato y preciso | 19 | 59 | `lfw2` |
| [3](3-harness-w3/) | **W3** Harness Engineering | y está estructurado y verifica | 22 | 85 | `lfw3` |
| [4](4-sandbox-w5/) | **W5** Sandbox Engineering | y es seguro | 24 | 118 | `lfw5` |
| [5](5-durable-w6/) | **W6** Durable Agents | y sobrevive a un crash | 26 | 133 | `lfw6` |
| [6](6-evals-w7/) | **W7** Agent Evals | y sabés si es bueno | 28 | 159 | `lfw7` |
| [7](7-skills-w4/) | **W4** Skills & Protocols | y es extensible | 31 | 177 | `lfw4` |
| [8](8-multiagent-w8/) | **W8** Coding Agents & Multi-Agent | y delega sin pagar el contexto | 33 | 184 | `lfw8` |

## Correr cualquiera

Desde la raíz del repo, con el `Makefile`. `WORLD` es el número de **paso**:

```bash
make test WORLD=3                        # los 85 tests que existían en ese punto
make ask WORLD=3 Q="explicame este proyecto"
make health WORLD=3
```

O a mano, si preferís ver las piezas:

```bash
cd worlds/3-harness-w3 && uv sync --extra dev && uv run lfw3 ask . "..."
```

Cada mundo tiene su propio comando (`lfw1`…`lfw8`, por número de **mundo**, no de paso) para que
puedas tener varios instalados sin que se pisen. `make worlds` muestra la correspondencia.

**La configuración del modelo es una sola**, en el `.env` de la raíz: el Makefile lo exporta a los
ocho. Y si el modelo configurado no está instalado, el provider busca uno que sí esté y que soporte
tool calling — así cualquier mundo arranca en cualquier máquina.

## Fotos y código vivo

Los pasos **1 a 7 son fotos**: se generan desde la historia de git con
[`scripts/build_worlds.py`](../scripts/build_worlds.py) y **no se editan a mano**, se regeneran.

El **paso 8 es el código vivo** del proyecto. Es el único que se edita: si mañana hay un Mundo 9, se
construye ahí y después se saca su foto.

```bash
python scripts/build_worlds.py          # regenera los pasos 1 a 7
python scripts/build_worlds.py --check  # verifica que estén
```

## Por qué el orden no es el del roadmap

Los pasos van en el orden en que el proyecto **se construyó**, y eso no es el orden de los números:
el **Mundo 5** (permisos) se hizo antes del **Mundo 4** (skills), porque los permisos eran
prerequisito duro de cualquier herramienta con efectos — la regla del proyecto era no agregar
`write_file` ni `run_command` sin ALLOW/ASK/DENY.

Ordenarlos por número rompería la propiedad que los hace útiles: el paso 4 tendría skills y el paso 5
las perdería. Así, **cada paso es el anterior más una cosa**, y los tests lo confirman — crecen
24 → 59 → 85 → 118 → 133 → 159 → 177 → 184 sin que ninguno se rompa.

## La explicación

El código dice *qué* hace; la guía dice *por qué*. Cada mundo de acá tiene su sección en
[`docs/GUIA.md`](../docs/GUIA.md), con preguntas antes de las respuestas:

### 👉 **https://localforge-guia.vercel.app**
