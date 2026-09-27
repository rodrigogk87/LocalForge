# Paso 2 · Mundo 2 — Context Engineering

> **Y es barato y preciso.**

Este directorio es un **proyecto Python completo e independiente** con el código tal cual estaba al
cerrar el Mundo 2. No tiene nada de los mundos posteriores: lo que leas acá es lo que existía
en ese momento, sin adelantos.

## Qué agrega este paso

context/ (estimador, capas, presupuesto, compactacion) y search_code

Se apoya en el **paso 1** (Python Agent Foundations): este proyecto es ese, más lo de arriba.

## Correrlo

```bash
uv sync --extra dev
uv run pytest -q
uv run lfw2 health
uv run lfw2 ask . "explicame este proyecto"
```

Necesitás Ollama con un modelo que soporte tool calling (`ollama show <modelo>` tiene que listar
`tools` en *Capabilities*).

## Por qué el orden no es el del roadmap

Los pasos van en el orden en que el proyecto se **construyó**, que no es el orden de los números.
El Mundo 5 (permisos) se hizo antes del Mundo 4 (skills), porque los permisos eran prerequisito duro
de cualquier herramienta con efectos. Cada paso se apoya en el anterior de verdad.

← [paso 1: Python Agent Foundations](../1-foundations-w1/) · [paso 3: Harness Engineering](../3-harness-w3/) →

---

*Generado por `scripts/build_worlds.py` desde el commit `49a0aaa`: es una **foto para leer**, no se edita a mano. El código vivo es el [paso 8](../8-multiagent-w8/).*
