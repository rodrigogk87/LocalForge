"""Worktrees (W8): subagentes que escriben sin tocar tu repo.

Los subagentes de la Fase 8 aislaban CONTEXTO: investigaban en su propia ventana
y devolvian una conclusion. Esto aisla el FILESYSTEM: un subagente que edita
trabaja en un `git worktree` -- otra carpeta, otro checkout del mismo repo, que
se tira al terminar. Lo que vuelve al padre es un diff.

    tu repo  ──(git worktree add)──>  /tmp/lf-wt-xxxx   el hijo escribe aca
       ^                                   │
       └──── diff + patch en disco <───────┘   nadie aplica nada solo

Por que worktree y no "copiar la carpeta":

- Es instantaneo y comparte el historial: el diff sale de `git diff`, exacto.
- Dos subagentes editando en paralelo trabajan en DOS worktrees: no se pisan.
  Es lo que hace posible el paralelismo real (el executor ya corre en paralelo
  las tool calls de un mismo turno).

Tres decisiones de seguridad:

1. **Nada se aplica solo.** El diff vuelve como texto y se guarda como .patch.
   Aplicarlo (`git apply`) es una decision humana.
2. **Sin hooks de git.** `git worktree add` dispara el hook post-checkout del
   repo; un repo hostil tendria ejecucion de codigo con solo delegarle una
   edicion. Todo comando git va con `core.hooksPath=/dev/null`.
3. **Se parte de HEAD.** Los cambios sin commitear del usuario NO estan en el
   worktree. Es una limitacion, y es la segura: no hay que copiar nada del
   working tree, y el diff es exactamente lo que hizo el hijo.

El hijo trae un verifier de RESULTADO (lo que la Fase 3 dejaba para "el dia que
el agente escriba codigo"): si dice que termino pero el diff esta vacio, se
rechaza. Decir "listo" sin cambiar nada es el equivalente de responder sin leer.
"""

from __future__ import annotations

import asyncio
import shutil
import tempfile
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, Field

from localforge.config import Settings, settings as default_settings
from localforge.harness.verify import Verdict
from localforge.models import AgentStatus, AgentTask
from localforge.permissions import Decision, PermissionPolicy, Rule, default_policy
from localforge.providers.base import ModelProvider
from localforge.tools.base import ToolError, ToolRegistry

NO_HOOKS = ("-c", "core.hooksPath=/dev/null")
MAX_DIFF_CHARS = 6_000


class GitError(RuntimeError):
    pass


async def git(cwd: Path, *args: str) -> str:
    proc = await asyncio.create_subprocess_exec(
        "git", *NO_HOOKS, *args, cwd=cwd,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    if proc.returncode != 0:
        raise GitError(f"git {' '.join(args)}: {err.decode(errors='replace').strip()}")
    return out.decode(errors="replace")


class Worktree:
    """`async with Worktree(repo) as path:` -> un checkout descartable de HEAD."""

    def __init__(self, repo: Path) -> None:
        self.repo = repo.resolve()
        self.path: Path | None = None

    async def __aenter__(self) -> Path:
        try:
            top = Path((await git(self.repo, "rev-parse", "--show-toplevel")).strip())
            await git(self.repo, "rev-parse", "--verify", "HEAD")
        except GitError:
            raise GitError(
                f"'{self.repo}' no es un repositorio git con al menos un commit: "
                "un worktree necesita un HEAD del que partir"
            ) from None
        self.repo = top
        self.path = Path(tempfile.mkdtemp(prefix="lf-wt-")) / "wt"
        await git(self.repo, "worktree", "add", "--detach", str(self.path), "HEAD")
        return self.path

    async def __aexit__(self, *exc: object) -> None:
        if self.path is None:
            return
        try:
            await git(self.repo, "worktree", "remove", "--force", str(self.path))
        except GitError:
            shutil.rmtree(self.path, ignore_errors=True)
            await git(self.repo, "worktree", "prune")
        shutil.rmtree(self.path.parent, ignore_errors=True)
        self.path = None


async def worktree_diff(path: Path) -> str:
    """Todo lo que cambio, incluidos archivos nuevos (por eso el `add -A`)."""
    await git(path, "add", "-A")
    return await git(path, "diff", "--cached", "HEAD")


class DiffVerifier:
    """Verifier de resultado: "termine" con el diff vacio se rechaza."""

    name = "diff"

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace

    def verify(self, task, answer, trajectory):  # noqa: ANN001, ANN201
        if "write_file" in trajectory:
            return Verdict.passed(self.name)
        return Verdict.rejected(
            self.name,
            "Dijiste que terminaste pero no modificaste ningun archivo. La tarea es un "
            "cambio: usa write_file con el contenido completo del archivo, y despues "
            "explicá en una linea que cambiaste.",
        )


def worktree_policy(base: PermissionPolicy | None = None) -> PermissionPolicy:
    """Adentro del worktree, escribir esta permitido sin preguntar.

    No es relajar la seguridad: el efecto queda en una carpeta temporal, y el
    control humano se movio al lugar donde importa -- aplicar o no el diff.
    Los secretos siguen denegados: la primera regla de la politica base va
    antes que esta.
    """
    policy = base or default_policy()
    deny_secrets = policy.rules[:1]
    rest = policy.rules[1:]
    policy.rules = deny_secrets + [
        Rule(tool="write_file", decision=Decision.ALLOW, reason="worktree descartable")
    ] + rest
    return policy


class EditArgs(BaseModel):
    objective: str = Field(
        min_length=1,
        description=(
            "El cambio concreto a hacer, autocontenido: el subagente no ve tu conversacion. "
            "Ej: 'en tools/fs.py, hacer que safe_path rechace rutas absolutas con un mensaje claro'."
        ),
    )
    max_turns: int = Field(default=10, ge=1, le=20)


class EditInWorktreeTool:
    name = "delegate_edit"
    description = (
        "Delega un CAMBIO de codigo a un subagente que trabaja en una copia aislada del repo "
        "(git worktree). Te devuelve el diff; tus archivos no se tocan. Podes pedir varios "
        "en el mismo turno: corren en paralelo, cada uno en su copia."
    )
    args_model = EditArgs

    def __init__(
        self,
        provider: ModelProvider,
        *,
        cfg: Settings | None = None,
        patches_dir: Path | None = None,
        on_event=None,  # noqa: ANN001
    ) -> None:
        self.provider = provider
        self.cfg = cfg or default_settings
        self.patches_dir = patches_dir or (self.cfg.state_dir / "patches")
        self.on_event = on_event

    async def run(self, workspace: Path, args: EditArgs) -> str:
        from localforge.harness.loop import AgentHarness  # diferido: ver subagent.py
        from localforge.permissions import AutoApprover
        from localforge.tools import default_registry
        from localforge.tools.write import WriteFileTool

        try:
            wt = Worktree(workspace)
            path = await wt.__aenter__()
        except GitError as exc:
            raise ToolError(str(exc)) from None
        try:
            registry: ToolRegistry = default_registry()
            registry.register(WriteFileTool())
            child = AgentHarness(
                self.provider,
                registry,
                cfg=self.cfg,
                policy=worktree_policy(),
                # Lo unico que podria preguntar es write_file, que en el worktree
                # esta permitido. El approver existe para lo que no se previo.
                approver=AutoApprover(),
                verifier=DiffVerifier(path),
                on_event=self.on_event,
            )
            task = AgentTask(
                objective=args.objective,
                repo_path=str(path),
                max_turns=args.max_turns,
                wall_clock_s=self.cfg.wall_clock_s,
                token_budget=self.cfg.token_budget,
            )
            outcome = await child.run(task)
            diff = await worktree_diff(path)
        finally:
            await wt.__aexit__(None, None, None)

        head = f"[subagente en worktree: {outcome.turns} turnos, {outcome.total_tokens} tokens]"
        if not diff.strip():
            motivo = outcome.reason.value if outcome.reason else outcome.status.value
            return f"{head}\nNo produjo cambios ({motivo}).\n{outcome.output or ''}".rstrip()

        self.patches_dir.mkdir(parents=True, exist_ok=True)
        patch = self.patches_dir / f"{uuid4().hex[:8]}.patch"
        patch.write_text(diff, encoding="utf-8")
        shown = diff if len(diff) <= MAX_DIFF_CHARS else diff[:MAX_DIFF_CHARS] + "\n[...diff cortado]"
        estado = "" if outcome.status is AgentStatus.COMPLETED else f" (termino: {outcome.reason.value if outcome.reason else outcome.status.value})"
        return (
            f"{head}{estado}\n{outcome.output}\n\n"
            f"DIFF (NO aplicado al repo; guardado en {patch}. Para aplicarlo: git apply {patch}):\n"
            f"{shown}"
        )


__all__ = [
    "Worktree", "worktree_diff", "DiffVerifier", "worktree_policy",
    "EditInWorktreeTool", "EditArgs", "GitError",
]
