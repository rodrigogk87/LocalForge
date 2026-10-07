# Paso 4 · Mundo 5 — Sandbox Engineering

> **Y es seguro.**

Este directorio es un **proyecto Python completo e independiente** con el código tal cual estaba al
cerrar el Mundo 5. No tiene nada de los mundos posteriores: lo que leas acá es lo que existía
en ese momento, sin adelantos.

## Qué agrega este paso

permisos ALLOW/ASK/DENY con fail-closed y aprobacion humana

Se apoya en el **paso 3** (Harness Engineering): este proyecto es ese, más lo de arriba.

## Correrlo

```bash
uv sync --extra dev
uv run pytest -q
uv run lfw5 health
uv run lfw5 ask . "explicame este proyecto"
```

Necesitás Ollama con un modelo que soporte tool calling (`ollama show <modelo>` tiene que listar
`tools` en *Capabilities*).

## Por qué el orden no es el del roadmap

Los pasos van en el orden en que el proyecto se **construyó**, que no es el orden de los números.
El Mundo 5 (permisos) se hizo antes del Mundo 4 (skills), porque los permisos eran prerequisito duro
de cualquier herramienta con efectos. Cada paso se apoya en el anterior de verdad.

← [paso 3: Harness Engineering](../3-harness-w3/) · [paso 5: Durable Agents](../5-durable-w6/) →

---

*Generado por `scripts/build_worlds.py` desde el commit `e2b48f9`: es una **foto para leer**, no se edita a mano. El código vivo es el [paso 9](../9-completo-w9/).*
