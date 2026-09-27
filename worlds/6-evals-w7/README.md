# Paso 6 · Mundo 7 — Agent Evals

> **Y sabes si es bueno.**

Este directorio es un **proyecto Python completo e independiente** con el código tal cual estaba al
cerrar el Mundo 7. No tiene nada de los mundos posteriores: lo que leas acá es lo que existía
en ese momento, sin adelantos.

## Qué agrega este paso

golden tasks, checks deterministas, taxonomia de fallos, comparador

Se apoya en el **paso 5** (Durable Agents): este proyecto es ese, más lo de arriba.

## Correrlo

```bash
uv sync --extra dev
uv run pytest -q
uv run lfw7 health
uv run lfw7 ask . "explicame este proyecto"
```

Necesitás Ollama con un modelo que soporte tool calling (`ollama show <modelo>` tiene que listar
`tools` en *Capabilities*).

## Por qué el orden no es el del roadmap

Los pasos van en el orden en que el proyecto se **construyó**, que no es el orden de los números.
El Mundo 5 (permisos) se hizo antes del Mundo 4 (skills), porque los permisos eran prerequisito duro
de cualquier herramienta con efectos. Cada paso se apoya en el anterior de verdad.

← [paso 5: Durable Agents](../5-durable-w6/) · [paso 7: Skills & Protocols](../7-skills-w4/) →

---

*Generado por `scripts/build_worlds.py` desde el commit `1808094`: es una **foto para leer**, no se edita a mano. El código vivo es el [paso 8](../8-multiagent-w8/).*
