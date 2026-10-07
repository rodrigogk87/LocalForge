# Paso 1 · Mundo 1 — Python Agent Foundations

> **El agente corre y termina.**

Este directorio es un **proyecto Python completo e independiente** con el código tal cual estaba al
cerrar el Mundo 1. No tiene nada de los mundos posteriores: lo que leas acá es lo que existía
en ese momento, sin adelantos.

## Qué agrega este paso

models.py, providers/, tools/ (list_files, read_file), harness/loop.py

Es el primero de los ocho.

## Correrlo

```bash
uv sync --extra dev
uv run pytest -q
uv run lfw1 health
uv run lfw1 ask . "explicame este proyecto"
```

Necesitás Ollama con un modelo que soporte tool calling (`ollama show <modelo>` tiene que listar
`tools` en *Capabilities*).

## Por qué el orden no es el del roadmap

Los pasos van en el orden en que el proyecto se **construyó**, que no es el orden de los números.
El Mundo 5 (permisos) se hizo antes del Mundo 4 (skills), porque los permisos eran prerequisito duro
de cualquier herramienta con efectos. Cada paso se apoya en el anterior de verdad.

[paso 2: Context Engineering](../2-context-w2/) →

---

*Generado por `scripts/build_worlds.py` desde el commit `7a3295d`: es una **foto para leer**, no se edita a mano. El código vivo es el [paso 9](../9-completo-w9/).*
