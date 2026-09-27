"""Tests de permisos (Fase 5).

Dos propiedades son las que importan y las dos son sobre lo que pasa cuando algo
NO esta previsto:

  - una tool que la politica no conoce se deniega (fail closed)
  - un ASK que nadie puede contestar se deniega

Todo lo demas es detalle. Un sistema de permisos que falla abierto no es un
sistema de permisos.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

from localforge.models import ToolCall
from localforge.permissions import (
    AutoApprover,
    Decision,
    DenyingApprover,
    PermissionPolicy,
    Rule,
    default_policy,
    read_only_policy,
)
from localforge.tools import ToolExecutor, default_registry


def call(name: str, **args) -> ToolCall:
    return ToolCall(id=f"c_{name}", name=name, arguments=args)


# --- fail closed ------------------------------------------------------------


def test_una_tool_desconocida_no_se_permite() -> None:
    """La propiedad mas importante del modulo."""
    policy = PermissionPolicy(rules=[])
    assert policy.decide(call("cualquier_cosa")).decision is Decision.DENY


def test_la_default_manda_las_tools_nuevas_a_ask() -> None:
    """Una tool con efectos que alguien agregue mañana cae en ASK sin tocar nada."""
    for futura in ("write_file", "run_command", "git_commit", "http_post"):
        assert default_policy().decide(call(futura, path="x.py")).decision is Decision.ASK


def test_las_tools_de_lectura_estan_permitidas() -> None:
    p = default_policy()
    for name in ("list_files", "search_code", "read_file"):
        assert p.decide(call(name, path="src/main.py")).decision is Decision.ALLOW


# --- secretos ---------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        ".env",
        ".env.local",
        "config/.env",
        "deploy/secrets.yaml",
        "keys/id_rsa",
        "certs/server.pem",
        "infra/prod.tfvars",
        ".git-credentials",
        "gcp/service-account-prod.json",
        "a/b/c/.npmrc",
    ],
)
def test_leer_un_secreto_se_deniega(path: str) -> None:
    verdict = default_policy().decide(call("read_file", path=path))
    assert verdict.decision is Decision.DENY, path
    # El motivo tiene que ser accionable para el modelo, no un "prohibido".
    assert "search_code" in verdict.reason or ".env.example" in verdict.reason


@pytest.mark.parametrize("path", ["src/main.py", "README.md", "docs/env.md", "environment.ts"])
def test_los_archivos_normales_no_se_confunden_con_secretos(path: str) -> None:
    assert default_policy().decide(call("read_file", path=path)).decision is Decision.ALLOW


def test_se_pueden_agregar_globs_sensibles() -> None:
    p = default_policy(extra_sensitive=("*.sqlite",))
    assert p.decide(call("read_file", path="data/app.sqlite")).decision is Decision.DENY


# --- reglas -----------------------------------------------------------------


def test_gana_la_primera_regla_que_matchea() -> None:
    p = PermissionPolicy(
        rules=[
            Rule(tool="read_file", decision=Decision.DENY, reason="primera"),
            Rule(tool="read_file", decision=Decision.ALLOW, reason="segunda"),
        ]
    )
    assert p.decide(call("read_file", path="x")).reason == "primera"


def test_una_regla_con_globs_no_matchea_sin_path() -> None:
    r = Rule(tool="read_file", decision=Decision.DENY, path_globs=(".env",))
    assert not r.matches(call("read_file"))


def test_el_comodin_matchea_cualquier_tool() -> None:
    p = PermissionPolicy(rules=[Rule(tool="*", decision=Decision.ALLOW)])
    assert p.decide(call("lo_que_sea")).decision is Decision.ALLOW


def test_read_only_policy_deniega_todo_lo_que_no_sea_lectura() -> None:
    p = read_only_policy()
    assert p.decide(call("read_file", path="a.py")).decision is Decision.ALLOW
    assert p.decide(call("write_file", path="a.py")).decision is Decision.DENY


# --- approvers --------------------------------------------------------------


async def test_el_approver_por_defecto_dice_no() -> None:
    assert not await DenyingApprover().approve(call("write_file"), "motivo")


async def test_auto_approver_dice_si() -> None:
    assert await AutoApprover().approve(call("write_file"), "motivo")


# --- integracion con el executor -------------------------------------------


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / "main.py").write_text("print('hola')\n", encoding="utf-8")
    (tmp_path / ".env").write_text("API_KEY=secreto-de-verdad\n", encoding="utf-8")
    return tmp_path


async def test_el_executor_no_lee_el_env_y_el_secreto_no_sale(repo: Path) -> None:
    """El test que de verdad importa: el contenido del .env no llega al contexto."""
    executor = ToolExecutor(default_registry(), repo)
    result = await executor.run_one(call("read_file", path=".env"))

    assert not result.success
    assert "permiso denegado" in result.error
    # Lo critico: el secreto no aparece en NINGUNA parte de lo que vuelve al modelo.
    assert "secreto-de-verdad" not in (result.error or "")
    assert "secreto-de-verdad" not in result.as_content()


async def test_el_executor_si_lee_un_archivo_normal(repo: Path) -> None:
    executor = ToolExecutor(default_registry(), repo)
    result = await executor.run_one(call("read_file", path="main.py"))
    assert result.success
    assert "hola" in result.output


async def test_una_tool_con_efectos_se_deniega_sin_approver(repo: Path) -> None:
    """ASK sin nadie a quien preguntar es DENY."""

    class Escritora:
        name = "write_file"
        description = "escribe"
        from pydantic import BaseModel

        class Args(BaseModel):
            path: str
            content: str = ""

        args_model = Args

        async def run(self, workspace, args):  # noqa: ANN001, ANN201
            (workspace / args.path).write_text(args.content, encoding="utf-8")
            return "escrito"

    registry = default_registry()
    registry.register(Escritora())
    executor = ToolExecutor(registry, repo)

    result = await executor.run_one(call("write_file", path="nuevo.txt", content="x"))
    assert not result.success
    assert "no fue aprobado" in result.error
    # Y sobre todo: NO escribio.
    assert not (repo / "nuevo.txt").exists()


async def test_con_approver_explicito_si_ejecuta(repo: Path) -> None:
    class Escritora:
        name = "write_file"
        description = "escribe"
        from pydantic import BaseModel

        class Args(BaseModel):
            path: str
            content: str = ""

        args_model = Args

        async def run(self, workspace, args):  # noqa: ANN001, ANN201
            (workspace / args.path).write_text(args.content, encoding="utf-8")
            return "escrito"

    registry = default_registry()
    registry.register(Escritora())
    executor = ToolExecutor(registry, repo, approver=AutoApprover())

    result = await executor.run_one(call("write_file", path="nuevo.txt", content="hola"))
    assert result.success
    assert (repo / "nuevo.txt").read_text(encoding="utf-8") == "hola"


async def test_el_permiso_se_evalua_antes_de_ejecutar(repo: Path) -> None:
    """Si el permiso se chequeara despues, el efecto ya estaria hecho."""
    llamadas: list[str] = []

    class Espia:
        name = "espia"
        description = "registra que corrio"
        from pydantic import BaseModel

        class Args(BaseModel):
            path: str = ""

        args_model = Args

        async def run(self, workspace, args):  # noqa: ANN001, ANN201
            llamadas.append("corri")
            return "ok"

    registry = default_registry()
    registry.register(Espia())
    executor = ToolExecutor(registry, repo)  # default: ASK -> DenyingApprover -> DENY

    result = await executor.run_one(call("espia"))
    assert not result.success
    assert llamadas == [], "la tool corrio a pesar del permiso denegado"


# --- no hay imports circulares ---------------------------------------------


@pytest.mark.parametrize(
    "primero",
    ["localforge.tools", "localforge.harness", "localforge.permissions", "localforge.cli"],
)
def test_el_paquete_importa_en_cualquier_orden(primero: str) -> None:
    """Regresion: permissions vivia en harness/ y el import era circular.

    Los tests pasaban igual porque la suite importaba en un orden que funcionaba.
    Este test fuerza cada orden en un interprete limpio.
    """
    for mod in [m for m in list(sys.modules) if m.startswith("localforge")]:
        del sys.modules[mod]
    importlib.import_module(primero)
    importlib.import_module("localforge.cli")
