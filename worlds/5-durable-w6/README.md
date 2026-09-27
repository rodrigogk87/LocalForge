# Paso 5 · Mundo 6 — Durable Agents

> **Y sobrevive a un crash.**

Este directorio es un **proyecto Python completo e independiente** con el código tal cual estaba al
cerrar el Mundo 6. No tiene nada de los mundos posteriores: lo que leas acá es lo que existía
en ese momento, sin adelantos.

## Qué agrega este paso

checkpoints atomicos por turno y resume idempotente

Se apoya en el **paso 4** (Sandbox Engineering): este proyecto es ese, más lo de arriba.

## Correrlo

```bash
uv sync --extra dev
uv run pytest -q
uv run lfw6 health
uv run lfw6 ask . "explicame este proyecto"
```

Necesitás Ollama con un modelo que soporte tool calling (`ollama show <modelo>` tiene que listar
`tools` en *Capabilities*).

## Por qué el orden no es el del roadmap

Los pasos van en el orden en que el proyecto se **construyó**, que no es el orden de los números.
El Mundo 5 (permisos) se hizo antes del Mundo 4 (skills), porque los permisos eran prerequisito duro
de cualquier herramienta con efectos. Cada paso se apoya en el anterior de verdad.

← [paso 4: Sandbox Engineering](../4-sandbox-w5/) · [paso 6: Agent Evals](../6-evals-w7/) →

---

*Generado por `scripts/build_worlds.py` desde el commit `24b24a5`: es una **foto para leer**, no se edita a mano. El código vivo es el [paso 8](../8-multiagent-w8/).*
