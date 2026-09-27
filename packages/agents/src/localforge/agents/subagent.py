"""Subagentes: aislamiento de contexto (W2-C13 + W8-C52).

Esta es la pieza que la Fase 2 dejo explicitamente pendiente. El docstring de
`harness/context.py` decia: *"aislamiento de contexto necesita subagentes, que
son W8"*. Acá estan.

El problema que resuelve. Una tarea como "entendé como funciona la
autenticacion" puede requerir leer diez archivos. Si el agente principal los lee
todos, su contexto se llena de diez archivos y en el turno siguiente arrastra
30k tokens de los cuales usa dos parrafos. El costo de investigar se paga en cada
turno posterior, para siempre.

Un subagente investiga en **su propia ventana** y devuelve solo la conclusion.
El padre paga los tokens del resumen, no los de la investigacion:

    padre:    "investiga la autenticacion"  -> 200 tokens de respuesta
    subagente: 10 archivos, 28k tokens      -> se descartan al terminar

Eso es lo unico que hace un subagente, y es mucho. No es paralelismo ni
especializacion: es **presupuesto de contexto**.

Tres propiedades que hay que tener para que no sea un pie en la trampa:

1. **Limite de profundidad.** Un subagente que puede crear subagentes es
   recursion sin caso base. Al llegar al limite, el registry del hijo NO incluye
   esta tool: no se le pide que se contenga, se le quita la posibilidad.

2. **El presupuesto del hijo sale del padre.** Si no, "delegá" se convierte en
   "ignorá los limites": diez subagentes con presupuesto propio gastan diez
   veces el presupuesto de la tarea.

3. **Un fallo del hijo es un resultado, no una excepcion.** Si el subagente se
   queda sin turnos, el padre lee "no pude" y sigue. Es la misma regla que
   cualquier otro ToolResult.

Este modulo vive fuera de `harness/` a proposito. Un subagente CONSTRUYE un
AgentHarness, y el harness no sabe que existen los subagentes: la flecha va en
una sola direccion. Mientras estuvo dentro de `harness/` habia que importar el
loop de forma diferida para no cerrar el ciclo.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from localforge.config import Settings, settings as default_settings
from localforge.models import AgentStatus, AgentTask
from localforge.sandbox import Approver, PermissionPolicy
from localforge.harness.loop import AgentHarness
from localforge.providers.base import ModelProvider
from localforge.tools.base import ToolError, ToolRegistry

# Cuanto puede anidarse. 1 significa "el principal puede delegar, el hijo no".
# Dos niveles ya son dificiles de razonar y el beneficio cae rapido.
MAX_DEPTH = 1


class SubagentArgs(BaseModel):
    objective: str = Field(
        min_length=1,
        description=(
            "La pregunta concreta que el subagente tiene que responder. Tiene que ser "
            "autocontenida: el subagente NO ve tu conversacion ni lo que leiste. "
            "Mal: 'seguí investigando eso'. Bien: 'en que archivo se define la "
            "validacion de tokens JWT y que algoritmo usa'."
        ),
    )
    max_turns: int = Field(
        default=8,
        ge=1,
        le=20,
        description="Turnos que le das. Sale de TU presupuesto, no es gratis.",
    )


class SubagentTool:
    """Delega una investigacion a un agente con contexto propio.

    Recibe el provider y una fabrica de registry en vez de construirlos: el
    subagente tiene que usar la misma inferencia y las mismas politicas que el
    padre, y eso solo se garantiza pasandoselas.
    """

    name = "delegate"
    description = (
        "Delega una investigacion a un subagente que trabaja en su PROPIO contexto y te "
        "devuelve solo la conclusion. Usalo cuando responder algo requiera leer varios "
        "archivos que despues no vas a necesitar: el costo de esa lectura no se suma a tu "
        "contexto, solo el resumen. La pregunta tiene que ser autocontenida, porque el "
        "subagente no ve nada de tu conversacion."
    )
    args_model = SubagentArgs

    def __init__(
        self,
        provider: ModelProvider,
        *,
        registry_factory,  # noqa: ANN001 - Callable[[int], ToolRegistry], evita import ciclico
        cfg: Settings | None = None,
        depth: int = 0,
        policy: PermissionPolicy | None = None,
        approver: Approver | None = None,
        on_event=None,  # noqa: ANN001
    ) -> None:
        self.provider = provider
        self.registry_factory = registry_factory
        self.cfg = cfg or default_settings
        self.depth = depth
        self.policy = policy
        self.approver = approver
        self.on_event = on_event

    async def run(self, workspace: Path, args: SubagentArgs) -> str:
        if self.depth >= MAX_DEPTH:
            # No deberia pasar: al llegar al limite la tool no se registra. Si
            # pasa, es un bug de armado y conviene que se vea.
            raise ToolError(
                f"limite de anidamiento alcanzado (profundidad {self.depth}). "
                "Resolvé esto vos mismo con read_file y search_code."
            )

        child_registry: ToolRegistry = self.registry_factory(self.depth + 1)

        # Contexto propio: el harness hijo construye su ContextBuilder desde cero,
        # asi que arranca con la ventana vacia. Ese es TODO el punto.
        child = AgentHarness(
            self.provider,
            child_registry,
            cfg=self.cfg,
            policy=self.policy,
            approver=self.approver,
            on_event=self.on_event,
            # El hijo no verifica: su salida la juzga el padre, que es quien sabe
            # para que la pidio. Verificar dos veces con el mismo criterio solo
            # duplica el costo.
            verifier=_AcceptAnything(),
        )

        task = AgentTask(
            objective=args.objective,
            repo_path=str(workspace),
            max_turns=args.max_turns,
            wall_clock_s=self.cfg.wall_clock_s,
            token_budget=self.cfg.token_budget,
        )
        outcome = await child.run(task)

        # Lo que vuelve al contexto del padre: la conclusion y el costo. NO la
        # trayectoria ni los archivos leidos -- eso se descarta, que es el punto.
        head = (
            f"[subagente: {outcome.turns} turnos, {outcome.total_tokens} tokens, "
            f"{len(outcome.trajectory)} llamadas a tools]"
        )
        if outcome.status is not AgentStatus.COMPLETED:
            motivo = outcome.reason.value if outcome.reason else "desconocido"
            return (
                f"{head}\nEl subagente NO pudo terminar ({motivo}).\n"
                f"Lo que alcanzo a concluir:\n{outcome.output or '(nada)'}\n"
                "Si necesitas esto, probá con una pregunta mas acotada o resolvelo vos."
            )
        return f"{head}\n{outcome.output}"


class _AcceptAnything:
    """Verifier permisivo para el hijo. Ver el comentario en run()."""

    name = "subagente"

    def verify(self, task, answer, trajectory):  # noqa: ANN001, ANN201
        from localforge.harness.verify import Verdict

        return Verdict.passed(self.name)


def registry_with_subagents(
    provider: ModelProvider,
    *,
    cfg: Settings | None = None,
    policy: PermissionPolicy | None = None,
    approver: Approver | None = None,
    on_event=None,  # noqa: ANN001
) -> ToolRegistry:
    """Arma el registry de solo lectura MAS la tool de delegacion.

    La fabrica se pasa a si misma hacia abajo con la profundidad incrementada,
    y al llegar a MAX_DEPTH deja de agregar `delegate`. El limite no depende de
    que el modelo se porte bien: a esa profundidad la tool no existe.
    """
    from localforge.tools import default_registry

    def build(depth: int) -> ToolRegistry:
        registry = default_registry()
        if depth < MAX_DEPTH:
            registry.register(
                SubagentTool(
                    provider,
                    registry_factory=build,
                    cfg=cfg,
                    depth=depth,
                    policy=policy,
                    approver=approver,
                    on_event=on_event,
                )
            )
        return registry

    return build(0)


__all__ = ["SubagentTool", "SubagentArgs", "registry_with_subagents", "MAX_DEPTH"]
