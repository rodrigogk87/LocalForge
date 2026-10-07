"""Sandbox (W5-C30..C33): contener lo que pasa cuando un comando corre.

Los permisos de la Fase 5 deciden *si* algo se ejecuta. Una vez que un comando
arranca, el permiso ya hizo todo lo que podia hacer: `rm -rf ~`, un `curl` a un
servidor ajeno o un fork bomb aprobados por un humano distraido corren igual.
Por eso la regla del proyecto era **"run_command no se agrega sin sandbox"**.
Este modulo es esa condicion.

El sandbox es un contenedor de Docker por comando, que se tira al terminar:

    red             --network none           ni exfiltrar ni descargar
    el repo         -v repo:/workspace:ro    puede leer, no puede modificar
    el resto del fs --read-only + tmpfs /tmp nada persiste fuera de /tmp
    recursos        --cpus --memory --pids-limit   ni minar ni fork bomb
    privilegios     --user 65534 --cap-drop ALL --security-opt no-new-privileges
    imagen          --pull never             nada se descarga sin que lo pidas

Y **falla cerrado**: si Docker no esta, no hay plan B que corra el comando en el
host. No existe una clase `LocalSandbox` a proposito -- un fallback "por ahora"
es exactamente el agujero que el sandbox vino a tapar. Sin Docker, `run_command`
no se registra, la politica lo deniega, y la tool misma se niega a correr: tres
capas, por si alguien saltea una.

Lo que NO contiene: el kernel es el del host (un contenedor no es una VM), y el
repo montado se puede LEER entero, secretos incluidos -- un `cat .env` dentro
del contenedor no pasa por la politica de `read_file`. La red cerrada evita que
salga del contenedor, pero el contenido vuelve al contexto del modelo como
salida del comando. Por eso run_command queda en ASK: un humano ve el comando.
"""

from __future__ import annotations

import asyncio
import os
import signal
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable
from uuid import uuid4


@dataclass(frozen=True)
class SandboxLimits:
    cpus: str = "1"
    memory: str = "512m"
    pids: int = 128
    tmp_size: str = "64m"
    timeout_s: float = 20.0


@dataclass(frozen=True)
class SandboxResult:
    exit_code: int | None
    stdout: str
    stderr: str
    duration_ms: int
    timed_out: bool = False


@runtime_checkable
class Sandbox(Protocol):
    name: str

    async def available(self) -> tuple[bool, str]: ...

    async def run(self, command: str, workspace: Path, *, timeout_s: float | None = None) -> SandboxResult: ...


async def _spawn(*argv: str) -> asyncio.subprocess.Process:
    # Sesion propia: el CLI de docker lanza procesos hijos que heredan los
    # pipes. Matar solo al padre los deja vivos con el pipe abierto, y asyncio
    # no da por terminado el proceso hasta que todos los pipes se cierran: el
    # "timeout" quedaba colgado para siempre. Con un grupo propio se mata todo.
    return await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=os.name != "nt",
    )


async def _terminate(proc: asyncio.subprocess.Process) -> None:
    """Mata el proceso y su grupo, y espera con techo: nunca se cuelga aca."""
    if proc.returncode is None:
        try:
            if os.name != "nt":
                os.killpg(proc.pid, signal.SIGKILL)
            else:
                proc.kill()
        except (ProcessLookupError, PermissionError):
            pass
    try:
        await asyncio.wait_for(proc.wait(), timeout=3)
    except asyncio.TimeoutError:
        pass


async def _exec(*argv: str, timeout: float) -> tuple[int | None, str, str]:
    try:
        proc = await _spawn(*argv)
    except (FileNotFoundError, PermissionError) as exc:
        return None, "", str(exc)
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        await _terminate(proc)
        return None, "", "timeout"
    return proc.returncode, out.decode(errors="replace"), err.decode(errors="replace")


class DockerSandbox:
    name = "docker"

    def __init__(
        self,
        image: str = "python:3.12-slim",
        limits: SandboxLimits | None = None,
        *,
        docker: str = "docker",
        probe_timeout_s: float = 8.0,
    ) -> None:
        self.image = image
        # Cuanto se espera a `docker version`. Un daemon colgado no responde
        # nunca: sin techo, --sandbox colgaba la CLI antes del primer turno.
        self.probe_timeout_s = probe_timeout_s
        self.limits = limits or SandboxLimits()
        self.docker = docker
        self._available: tuple[bool, str] | None = None

    def argv(self, command: str, workspace: Path, name: str) -> list[str]:
        """La linea de `docker run`. Separada para poder leerla y testearla:
        cada flag es una propiedad de seguridad (ver el docstring del modulo)."""
        lim = self.limits
        return [
            self.docker, "run", "--rm",
            "--name", name,
            "--pull", "never",
            "--network", "none",
            "--cpus", lim.cpus,
            "--memory", lim.memory,
            "--memory-swap", lim.memory,
            "--pids-limit", str(lim.pids),
            "--read-only",
            "--tmpfs", f"/tmp:rw,nosuid,size={lim.tmp_size}",
            "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            "--user", "65534:65534",
            "-e", "HOME=/tmp",
            "-e", "PYTHONDONTWRITEBYTECODE=1",
            "-v", f"{workspace.resolve()}:/workspace:ro",
            "-w", "/workspace",
            self.image,
            "sh", "-c", command,
        ]

    async def available(self) -> tuple[bool, str]:
        """¿Hay daemon y esta la imagen? Se cachea: preguntarlo por comando
        cuesta medio segundo cada vez."""
        if self._available is not None:
            return self._available
        code, out, err = await _exec(self.docker, "version", "--format", "{{.Server.Version}}", timeout=self.probe_timeout_s)
        if code != 0:
            if code is None and err != "timeout":
                motivo = "no se encontro el comando docker"
            else:
                motivo = "el daemon de Docker no responde (¿Docker Desktop esta abierto?)"
            self._available = (False, motivo)
            return self._available
        code, _, _ = await _exec(self.docker, "image", "inspect", self.image, timeout=self.probe_timeout_s)
        if code != 0:
            self._available = (
                False,
                f"falta la imagen '{self.image}'. No se descarga sola: corré `docker pull {self.image}`",
            )
            return self._available
        self._available = (True, f"docker {out.strip()} · {self.image}")
        return self._available

    async def run(self, command: str, workspace: Path, *, timeout_s: float | None = None) -> SandboxResult:
        timeout = timeout_s or self.limits.timeout_s
        name = f"localforge-{uuid4().hex[:12]}"
        started = time.monotonic()
        proc = await _spawn(*self.argv(command, workspace, name))
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            await self._kill(name, proc)
            return SandboxResult(None, "", "", int((time.monotonic() - started) * 1000), timed_out=True)
        except asyncio.CancelledError:
            # Cancelar la corrutina NO para el contenedor: matar el cliente
            # `docker run` deja el contenedor vivo. Hay que matarlo por nombre.
            await asyncio.shield(self._kill(name, proc))
            raise
        return SandboxResult(
            proc.returncode,
            out.decode(errors="replace"),
            err.decode(errors="replace"),
            int((time.monotonic() - started) * 1000),
        )

    async def _kill(self, name: str, proc: asyncio.subprocess.Process) -> None:
        await _exec(self.docker, "kill", name, timeout=10)
        await _terminate(proc)


__all__ = ["Sandbox", "DockerSandbox", "SandboxLimits", "SandboxResult"]
