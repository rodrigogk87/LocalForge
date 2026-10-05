# LocalForge — un comando para cualquier mundo.
#
# El repositorio son ocho proyectos Python independientes, uno por mundo. Este
# Makefile es el unico lugar donde viven los comandos, y el que centraliza la
# configuracion del provider: incluye el `.env` de la raiz y lo EXPORTA, asi los
# ocho mundos ven el mismo modelo -- incluido el Mundo 1, cuyo codigo todavia no
# sabia leer un `.env` (esa feature llego despues).
#
#   make                     esta ayuda
#   make test WORLD=3        los tests del paso 3
#   make test-all            los ocho, con resumen
#   make ask WORLD=1 Q="..." correr el agente
#
# WORLD es el numero de PASO (1..8), no el del mundo del roadmap. `make worlds`
# los lista con su correspondencia.

# --- Windows: las recetas son POSIX sh ---------------------------------------
# Lanzado desde PowerShell/cmd, make cae a cmd.exe si no hay `sh` en el PATH, y
# `test`, `{ ...; }`, grep o sed no existen. Usamos el sh de Git for Windows
# (y sus utilidades de usr/bin), ubicado a partir de `git --exec-path`.
ifeq ($(OS),Windows_NT)
GIT_ROOT := $(subst /mingw64/libexec/git-core,,$(shell git --exec-path))
export PATH := $(GIT_ROOT)/usr/bin;$(PATH)
SHELL := sh.exe
endif

WORLD ?= 8
Q ?= Explicame este proyecto
REPO ?= .

# --- la configuracion del provider, en un solo lugar -----------------------
# Incluir y exportar el .env es lo que hace que no haya que repetir
# LOCALFORGE_MODEL=... en cada comando ni en cada mundo.
ifneq (,$(wildcard .env))
include .env
export
endif

WORLD_DIR := $(firstword $(wildcard worlds/$(WORLD)-*))
CMD := lf$(lastword $(subst -, ,$(notdir $(WORLD_DIR))))
ALL_DIRS := $(sort $(wildcard worlds/*-w*))

.DEFAULT_GOAL := help
.PHONY: help worlds setup setup-all test test-all ask health eval runs resume \
        lab-context lab-harness lab-verify repo-test docs docs-check build clean check

## help: esta ayuda
help:
	@printf "\nLocalForge — ocho mundos, un Makefile\n\n"
	@grep -hE '^## ' $(MAKEFILE_LIST) | sed 's/## /  make /' | column -t -s ':'
	@printf "\n  WORLD=$(WORLD) → $(WORLD_DIR) (comando: $(CMD))\n"
	@printf "  modelo: $${LOCALFORGE_MODEL:-<autodetectado>}\n\n"

## worlds: lista los ocho pasos y a que mundo corresponden
worlds:
	@printf "  paso  mundo  carpeta                   comando  tests\n"
	@for d in $(ALL_DIRS); do \
	  n=$$(basename $$d | cut -d- -f1); \
	  w=$$(basename $$d | sed 's/.*-w//'); \
	  t=$$(ls $$d/tests/test_*.py 2>/dev/null | wc -l | tr -d ' '); \
	  printf "   %-4s  W%-4s  %-24s  lfw%-4s  %s archivos\n" "$$n" "$$w" "$$(basename $$d)" "$$w" "$$t"; \
	done

## setup: instala las dependencias de un mundo (WORLD=n)
setup: guard
	@cd $(WORLD_DIR) && uv sync --extra dev

## setup-all: instala los ocho
setup-all:
	@for d in $(ALL_DIRS); do printf "  %-24s " "$$(basename $$d)"; (cd $$d && uv sync -q --extra dev) && echo ok; done

## test: corre los tests de un mundo (WORLD=n)
test: guard
	@cd $(WORLD_DIR) && uv run pytest -q

## test-all: corre los ocho y muestra el resumen
test-all:
	@fail=0; for d in $(ALL_DIRS); do \
	  printf "  %-24s " "$$(basename $$d)"; \
	  out=$$(cd $$d && uv run --no-sync pytest -q 2>&1 | tail -1); \
	  echo "$$out"; \
	  case "$$out" in *failed*|*error*) fail=1;; esac; \
	done; exit $$fail

## health: verifica que el LLM local responda (WORLD=n)
health: guard
	@cd $(WORLD_DIR) && uv run $(CMD) health

## ask: corre el agente (WORLD=n Q="pregunta" REPO=ruta)
ask: guard
	@cd $(WORLD_DIR) && uv run $(CMD) ask "$(REPO)" "$(Q)" $(FLAGS)

## eval: corre el dataset de golden tasks (necesita WORLD>=6)
eval: guard
	@cd $(WORLD_DIR) && uv run $(CMD) eval "$(REPO)"

## runs: lista las corridas guardadas (necesita WORLD>=5)
runs: guard
	@cd $(WORLD_DIR) && uv run $(CMD) runs

## resume: retoma una corrida (ID=xxx, necesita WORLD>=5)
resume: guard
	@cd $(WORLD_DIR) && uv run $(CMD) resume "$(ID)"

## lab-context: laboratorio del Mundo 2, ContextBuilder compactando sin LLM
lab-context:
	@cd $(firstword $(wildcard worlds/2-*)) && uv run python ../../labs/w2_context_builder.py

## lab-harness: laboratorio del Mundo 3, estados + verifier + repair loop sin LLM
lab-harness:
	@cd $(firstword $(wildcard worlds/3-*)) && uv run python ../../labs/w3_harness_verify.py

## lab-verify: alias de lab-harness
lab-verify: lab-harness

## repo-test: los tests del repo (coherencia de los ocho + la doc)
repo-test:
	@uv run pytest -q

## docs: reescribe las lineas que las guias citan del codigo
docs:
	@uv run python scripts/sync_code_refs.py

## docs-check: falla si las guias citan lineas que ya no corresponden
docs-check:
	@uv run python scripts/sync_code_refs.py --check

## build: regenera las fotos de los pasos 1 a 7 desde git
build:
	@uv run python scripts/build_worlds.py
	@uv run python scripts/patch_worlds.py

## check: todo lo que tiene que estar en verde antes de un commit
check: docs-check repo-test test-all

## clean: borra venvs, caches y lockfiles de los mundos
clean:
	@rm -rf .pytest_cache .ruff_cache
	@for d in $(ALL_DIRS); do rm -rf $$d/.venv $$d/uv.lock $$d/.pytest_cache; done
	@find worlds -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null || true
	@echo "  limpio. `make setup-all` para volver a instalar."

.PHONY: guard
guard:
	@test -n "$(WORLD_DIR)" || { echo "WORLD=$(WORLD) no existe. Probá: make worlds"; exit 1; }
