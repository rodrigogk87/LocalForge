"""Sandbox y run_command (W5, la mitad que faltaba).

La regla del proyecto era "run_command no se agrega sin sandbox". Estos tests
verifican la regla en sus tres capas (registro, politica, la tool misma) y las
propiedades de contencion de la linea de `docker run`.

No necesitan Docker: el contenedor se reemplaza por un `docker` falso (un script
que anota con que argumentos lo llamaron). Hay UN test que usa Docker de verdad,
opt-in con LOCALFORGE_DOCKER_TESTS=1: preguntarle a un daemon colgado puede
tardar segundos, y la suite tiene que ser rapida y determinista.
"""

from __future__ import annotations

import asyncio
import os
import stat
from pathlib import Path

import pytest

from localforge.models import ToolCall
from localforge.permissions import Decision, default_policy
from localforge.sandbox import DockerSandbox, SandboxLimits, SandboxResult
from localforge.tools import ToolError, default_registry
from localforge.tools.command import (
    RunCommandArgs,
    RunCommandTool,
    command_policy,
    registry_with_sandbox,
)


class FakeSandbox:
    name = "fake"

    def __init__(self, ok: bool = True, result: SandboxResult | None = None) -> None:
        self.ok = ok
        self.result = result or SandboxResult(0, "3 passed\n", "", 1200)
        self.commands: list[str] = []

    async def available(self):  # noqa: ANN201
        return (True, "fake") if self.ok else (False, "no hay docker")

    async def run(self, command, workspace, *, timeout_s=None):  # noqa: ANN001, ANN201
        self.commands.append(command)
        return self.result


def call(cmd: str = "pytest -q") -> ToolCall:
    return ToolCall(id="c", name="run_command", arguments={"command": cmd})


# --- la linea de docker run -----------------------------------------------------------


def test_la_linea_de_docker_tiene_cada_propiedad_de_contencion(tmp_path: Path) -> None:
    argv = DockerSandbox("img:1", SandboxLimits(cpus="2", memory="256m", pids=64)).argv(
        "pytest", tmp_path, "lf-x"
    )
    linea = " ".join(argv)
    assert "--network none" in linea, "con red"
    assert f"{tmp_path.resolve()}:/workspace:ro" in linea, "el repo no esta en solo lectura"
    assert "--read-only" in argv
    assert "--cap-drop ALL" in linea
    assert "--security-opt no-new-privileges" in linea
    assert "--user 65534:65534" in linea, "corre como root"
    assert "--pids-limit 64" in linea and "--memory 256m" in linea and "--cpus 2" in linea
    assert "--pull never" in linea, "podria descargar una imagen sin avisar"
    assert "--rm" in argv
    assert argv[-3:] == ["sh", "-c", "pytest"]


# --- docker falso ------------------------------------------------------------------------


def fake_docker(tmp_path: Path, *, version_ok: bool = True, image_ok: bool = True, sleep: float = 0) -> Path:
    """Un ejecutable `docker` que anota sus argumentos y simula respuestas."""
    log = tmp_path / "docker.log"
    script = tmp_path / "docker"
    script.write_text(
        f"""#!/bin/sh
echo "$@" >> {log}
case "$1" in
  version) {'echo 27.0.0' if version_ok else 'echo "Cannot connect" >&2; exit 1'} ;;
  image) {'exit 0' if image_ok else 'exit 1'} ;;
  kill) exit 0 ;;
  run) {f'exec sleep {sleep}' if sleep else 'echo "salida del comando"; echo "un warning" >&2; exit 3'} ;;
esac
""",
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script


needs_sh = pytest.mark.skipif(os.name == "nt", reason="el docker falso es un script sh")


@needs_sh
async def test_sin_daemon_no_esta_disponible(tmp_path: Path) -> None:
    sb = DockerSandbox(docker=str(fake_docker(tmp_path, version_ok=False)))
    ok, motivo = await sb.available()
    assert not ok and "daemon" in motivo


@needs_sh
async def test_sin_imagen_no_se_descarga_sola(tmp_path: Path) -> None:
    sb = DockerSandbox("python:3.12-slim", docker=str(fake_docker(tmp_path, image_ok=False)))
    ok, motivo = await sb.available()
    assert not ok and "docker pull python:3.12-slim" in motivo
    assert "pull" not in (tmp_path / "docker.log").read_text().split("\n")[-2], "intento descargar"


@needs_sh
async def test_un_daemon_colgado_no_cuelga_la_cli(tmp_path: Path) -> None:
    """Regresion de una corrida real: `docker version` contra un daemon colgado
    no respondia, y el CLI deja hijos con el pipe abierto. Matar solo al padre
    no alcanzaba: asyncio esperaba el pipe para siempre y `--sandbox` colgaba
    la CLI antes del primer turno."""
    script = tmp_path / "docker"
    script.write_text("#!/bin/sh\n(sleep 30) &\nsleep 30\n", encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    sb = DockerSandbox(docker=str(script), probe_timeout_s=0.5)
    ok, motivo = await asyncio.wait_for(sb.available(), timeout=6)
    assert not ok and "no responde" in motivo


async def test_sin_el_ejecutable_no_esta_disponible(tmp_path: Path) -> None:
    ok, motivo = await DockerSandbox(docker=str(tmp_path / "no-existe")).available()
    assert not ok and "no se encontro" in motivo


@needs_sh
async def test_corre_y_devuelve_salida_y_exit_code(tmp_path: Path) -> None:
    sb = DockerSandbox(docker=str(fake_docker(tmp_path)))
    res = await sb.run("pytest", tmp_path)
    assert res.exit_code == 3 and "salida del comando" in res.stdout and "warning" in res.stderr


@needs_sh
async def test_el_timeout_mata_el_contenedor_por_nombre(tmp_path: Path) -> None:
    sb = DockerSandbox(docker=str(fake_docker(tmp_path, sleep=5)))
    res = await sb.run("sleep 99", tmp_path, timeout_s=0.3)
    assert res.timed_out
    log = (tmp_path / "docker.log").read_text()
    assert "kill localforge-" in log, "el contenedor quedo vivo"


@needs_sh
async def test_cancelar_desde_afuera_tambien_mata_el_contenedor(tmp_path: Path) -> None:
    """El executor envuelve la tool en wait_for: cancelar la corrutina no para un
    `docker run`, hay que matar el contenedor por nombre."""
    sb = DockerSandbox(docker=str(fake_docker(tmp_path, sleep=5)))
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(sb.run("sleep 99", tmp_path, timeout_s=20), timeout=0.3)
    assert "kill localforge-" in (tmp_path / "docker.log").read_text()


# --- las tres capas ----------------------------------------------------------------------


async def test_capa_1_sin_sandbox_la_tool_no_se_registra() -> None:
    reg, motivo = await registry_with_sandbox(default_registry(), FakeSandbox(ok=False))
    assert "run_command" not in reg.names() and "no hay docker" in motivo
    reg, _ = await registry_with_sandbox(default_registry(), None)
    assert "run_command" not in reg.names()
    reg, _ = await registry_with_sandbox(default_registry(), FakeSandbox())
    assert "run_command" in reg.names()


def test_capa_2_la_politica_deniega_sin_sandbox_y_pregunta_con_sandbox() -> None:
    assert command_policy(False).decide(call()).decision is Decision.DENY
    assert command_policy(True).decide(call()).decision is Decision.ASK
    # Y sin la regla, la politica de siempre ya la mandaba a ASK: nunca ALLOW.
    assert default_policy().decide(call()).decision is Decision.ASK


async def test_capa_3_la_tool_no_corre_sin_sandbox(tmp_path: Path) -> None:
    with pytest.raises(ToolError, match="no corre en el host"):
        await RunCommandTool(None).run(tmp_path, RunCommandArgs(command="ls"))
    with pytest.raises(ToolError, match="no esta disponible"):
        await RunCommandTool(FakeSandbox(ok=False)).run(tmp_path, RunCommandArgs(command="ls"))


async def test_un_exit_distinto_de_cero_es_un_resultado_no_un_error(tmp_path: Path) -> None:
    sb = FakeSandbox(result=SandboxResult(1, "1 failed\n", "AssertionError\n", 900))
    out = await RunCommandTool(sb).run(tmp_path, RunCommandArgs(command="pytest -q"))
    assert "[exit 1" in out and "1 failed" in out and "--- stderr ---" in out


async def test_un_timeout_si_es_un_error_que_dice_que_hacer(tmp_path: Path) -> None:
    sb = FakeSandbox(result=SandboxResult(None, "", "", 20000, timed_out=True))
    with pytest.raises(ToolError, match="Acotalo"):
        await RunCommandTool(sb).run(tmp_path, RunCommandArgs(command="pytest"))


def test_run_command_no_cuenta_como_evidencia_de_lectura() -> None:
    """Correr los tests dice si algo FUNCIONA, no que dice el codigo."""
    from localforge.harness.verify import EVIDENCE_TOOLS

    assert "run_command" not in EVIDENCE_TOOLS


# --- con Docker de verdad ------------------------------------------------------------------


@pytest.mark.skipif(
    os.environ.get("LOCALFORGE_DOCKER_TESTS") != "1",
    reason="opt-in: LOCALFORGE_DOCKER_TESTS=1 (necesita Docker y la imagen python:3.12-slim)",
)
async def test_docker_real_sin_red_y_repo_de_solo_lectura(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("hola", encoding="utf-8")
    sb = DockerSandbox("python:3.12-slim")
    escribir = await sb.run("echo x > /workspace/b.txt", tmp_path, timeout_s=30)
    assert escribir.exit_code != 0 and not (tmp_path / "b.txt").exists()
    red = await sb.run("python -c \"import socket; socket.create_connection(('1.1.1.1', 53), 2)\"", tmp_path, timeout_s=30)
    assert red.exit_code != 0
    leer = await sb.run("cat a.txt", tmp_path, timeout_s=30)
    assert leer.stdout == "hola"
