# Paso 8 · Mundo 8 — Coding Agents & Multi-Agent

> **Y delega sin pagar el contexto.**

Este directorio es un **proyecto Python completo e independiente** con el código tal cual estaba al
cerrar el Mundo 8. No tiene nada de los mundos posteriores: lo que leas acá es lo que existía
en ese momento, sin adelantos.

## Qué agrega este paso

subagentes con contexto aislado

Se apoya en el **paso 7** (Skills & Protocols): este proyecto es ese, más lo de arriba.

## Correrlo

```bash
uv sync --extra dev
uv run pytest -q
uv run lfw8 health
uv run lfw8 ask . "explicame este proyecto"
```

Necesitás Ollama con un modelo que soporte tool calling (`ollama show <modelo>` tiene que listar
`tools` en *Capabilities*).

## Por qué el orden no es el del roadmap

Los pasos van en el orden en que el proyecto se **construyó**, que no es el orden de los números.
El Mundo 5 (permisos) se hizo antes del Mundo 4 (skills), porque los permisos eran prerequisito duro
de cualquier herramienta con efectos. Cada paso se apoya en el anterior de verdad.

← [paso 7: Skills & Protocols](../7-skills-w4/)

---

*Generado por `scripts/build_worlds.py` desde el commit `5f62eb4`. El código vivo del proyecto
está en [`packages/`](../../packages/); esto es una foto para leer.*
