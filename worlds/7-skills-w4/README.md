# Paso 7 · Mundo 4 — Skills & Protocols

> **Y es extensible.**

Este directorio es un **proyecto Python completo e independiente** con el código tal cual estaba al
cerrar el Mundo 4. No tiene nada de los mundos posteriores: lo que leas acá es lo que existía
en ese momento, sin adelantos.

## Qué agrega este paso

skills con progressive disclosure: una linea por skill en el prompt

Se apoya en el **paso 6** (Agent Evals): este proyecto es ese, más lo de arriba.

## Correrlo

```bash
uv sync --extra dev
uv run pytest -q
uv run lfw4 health
uv run lfw4 ask . "explicame este proyecto"
```

Necesitás Ollama con un modelo que soporte tool calling (`ollama show <modelo>` tiene que listar
`tools` en *Capabilities*).

## Por qué el orden no es el del roadmap

Los pasos van en el orden en que el proyecto se **construyó**, que no es el orden de los números.
El Mundo 5 (permisos) se hizo antes del Mundo 4 (skills), porque los permisos eran prerequisito duro
de cualquier herramienta con efectos. Cada paso se apoya en el anterior de verdad.

← [paso 6: Agent Evals](../6-evals-w7/) · [paso 8: Coding Agents & Multi-Agent](../8-multiagent-w8/) →

---

*Generado por `scripts/build_worlds.py` desde el commit `5f62eb4`: es una **foto para leer**, no se edita a mano. El código vivo es el [paso 9](../9-completo-w9/).*
