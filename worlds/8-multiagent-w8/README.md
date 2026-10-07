# Paso 8 · Mundo 8 — Coding Agents & Multi-Agent

> **Y delega sin pagar el contexto.**

Este directorio es un **proyecto Python completo e independiente**: los ocho mundos tal como se
cerraron, con lo que cada uno dejó pendiente todavía pendiente. Lo que falta se completa en el
[paso 9](../9-completo-w9/).

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

← [paso 7: Skills & Protocols](../7-skills-w4/) · [paso 9: los ocho mundos, completos](../9-completo-w9/) →

---

*Este paso no se regenera ni se edita: es una foto congelada a mano. Salió del commit `5f62eb4` y
después sumó dos cosas antes de congelarse: el flag `--delegate` (la tool existía pero no había forma
de llamarla desde la CLI) y las anotaciones por mundo en `models.py`. Los pasos 1 a 7 son fotos
generadas desde la historia de git con [`scripts/build_worlds.py`](../../scripts/build_worlds.py);
el código vivo es el [paso 9](../9-completo-w9/).*
