"""Worktrees (W8, lo que el mundo dejo pendiente).

La propiedad central: **el repo del usuario no se toca.** El subagente escribe en
un worktree descartable y lo que vuelve es un diff. Se prueba ademas que dos
ediciones en paralelo no se pisan, que los hooks del repo no corren, y el
verifier de resultado (un "listo" sin diff se rechaza).
"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest

from localforge.config import Settings
from localforge.harness.worktree import (
    DiffVerifier,
    EditArgs,
    EditInWorktreeTool,
    GitError,
    Worktree,
    worktree_policy,
)
from localforge.models import AgentTask, ModelResponse, StopReason, ToolCall
from localforge.permissions import Decision
from localforge.tools import ToolError


def sh(cwd: Path, *args: str) -> str:
    return subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True).stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    r.mkdir()
    sh(r, "git", "init", "-q")
    sh(r, "git", "config", "user.email", "t@t")
    sh(r, "git", "config", "user.name", "t")
    (r / "app.py").write_text("def main():\n    return 1\n", encoding="utf-8")
    sh(r, "git", "add", "-A")
    sh(r, "git", "commit", "-qm", "init")
    return r


class PorObjetivo:
    """Provider con un guion por objetivo (cada subagente tiene el suyo)."""

    name = model = "s"

    def __init__(self, guiones: dict[str, list[ModelResponse]]) -> None:
        self.guiones = {k: list(v) for k, v in guiones.items()}

    async def complete(self, messages, tools=None, *, system=None, max_tokens=None):  # noqa: ANN001
        objetivo = messages[0].content or ""
        for clave, rs in self.guiones.items():
            if clave in objetivo and rs:
                return rs.pop(0)
        return ModelResponse(content="listo", stop_reason=StopReason.END_TURN)

    async def health(self):  # noqa: ANN201
        return {}

    async def aclose(self) -> None:
        return None


def write(path: str, content: str, cid: str = "w") -> ModelResponse:
    return ModelResponse(
        tool_calls=[ToolCall(id=cid, name="write_file", arguments={"path": path, "content": content})],
        stop_reason=StopReason.TOOL_USE,
    )


def text(c: str) -> ModelResponse:
    return ModelResponse(content=c, stop_reason=StopReason.END_TURN)


def tool(provider, tmp_path: Path) -> EditInWorktreeTool:  # noqa: ANN001
    return EditInWorktreeTool(provider, cfg=Settings(), patches_dir=tmp_path / "patches")


async def test_el_cambio_vuelve_como_diff_y_el_repo_no_se_toca(repo: Path, tmp_path: Path) -> None:
    p = PorObjetivo({"main": [write("app.py", "def main():\n    return 2\n"), text("main devuelve 2")]})
    out = await tool(p, tmp_path).run(repo, EditArgs(objective="hacer que main devuelva 2"))
    assert "-    return 1" in out and "+    return 2" in out
    assert "NO aplicado" in out
    assert (repo / "app.py").read_text() == "def main():\n    return 1\n", "toco el repo real"
    assert sh(repo, "git", "status", "--porcelain") == ""
    # El worktree se borro y git no lo recuerda.
    assert sh(repo, "git", "worktree", "list").count("\n") == 1


async def test_el_patch_guardado_se_aplica_limpio(repo: Path, tmp_path: Path) -> None:
    p = PorObjetivo({"nuevo": [write("util.py", "X = 1\n"), text("agregue util.py")]})
    out = await tool(p, tmp_path).run(repo, EditArgs(objective="crear un archivo nuevo util.py"))
    patch = next((tmp_path / "patches").glob("*.patch"))
    assert "util.py" in out
    sh(repo, "git", "apply", str(patch))
    assert (repo / "util.py").read_text() == "X = 1\n"


async def test_dos_ediciones_en_paralelo_no_se_pisan(repo: Path, tmp_path: Path) -> None:
    p = PorObjetivo(
        {
            "A": [write("app.py", "def main():\n    return 'A'\n", "a"), text("A")],
            "B": [write("app.py", "def main():\n    return 'B'\n", "b"), text("B")],
        }
    )
    t = tool(p, tmp_path)
    da, db = await asyncio.gather(
        t.run(repo, EditArgs(objective="version A")), t.run(repo, EditArgs(objective="version B"))
    )
    assert "+    return 'A'" in da and "'B'" not in da
    assert "+    return 'B'" in db and "'A'" not in db


async def test_un_listo_sin_cambios_se_rechaza_y_se_repara(repo: Path, tmp_path: Path) -> None:
    p = PorObjetivo({"main": [text("listo, ya esta"), write("app.py", "def main():\n    return 3\n"), text("ahora si")]})
    out = await tool(p, tmp_path).run(repo, EditArgs(objective="hacer que main devuelva 3"))
    assert "+    return 3" in out


def test_el_diff_verifier() -> None:
    v = DiffVerifier(Path("."))
    t = AgentTask(objective="x", repo_path=".")
    assert not v.verify(t, "listo", ["read_file"]).ok
    assert v.verify(t, "listo", ["read_file", "write_file"]).ok


def test_en_el_worktree_se_escribe_sin_preguntar_pero_los_secretos_siguen_denegados() -> None:
    pol = worktree_policy()
    ok = pol.decide(ToolCall(id="1", name="write_file", arguments={"path": "a.py", "content": ""}))
    secreto = pol.decide(ToolCall(id="2", name="write_file", arguments={"path": ".env", "content": ""}))
    assert ok.decision is Decision.ALLOW
    assert secreto.decision is Decision.DENY


async def test_los_hooks_del_repo_no_corren(repo: Path, tmp_path: Path) -> None:
    """`git worktree add` dispara post-checkout. Un repo hostil lo usaria."""
    marca = tmp_path / "HOOK_CORRIO"
    hook = repo / ".git" / "hooks" / "post-checkout"
    hook.write_text(f"#!/bin/sh\ntouch {marca}\n", encoding="utf-8")
    hook.chmod(0o755)
    async with Worktree(repo):
        pass
    assert not marca.exists(), "el hook del repo se ejecuto"


async def test_sin_git_es_un_error_que_explica(tmp_path: Path) -> None:
    with pytest.raises(GitError, match="al menos un commit"):
        async with Worktree(tmp_path):
            pass
    with pytest.raises(ToolError, match="repositorio git"):
        await tool(PorObjetivo({}), tmp_path).run(tmp_path, EditArgs(objective="x"))
