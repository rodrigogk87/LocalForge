"""Laboratorio de context engineering: STATE completo -> ContextBuilder.build() -> BuiltContext.

No es un test de pytest: es una demo para correr a mano y LEER la salida.

    make lab-context

Vive en `labs/` y no adentro de `worlds/2-context-w2/` porque el paso 2 es una
foto generada: `make build` la borra y la rehace desde git. El Makefile la corre
con el entorno de ese paso, que es el que tiene `localforge` instalado.

Sin red, sin Ollama, sin mocks: solo AgentMessage, ContextBudget, ContextBuilder,
TokenEstimator y BuiltContext, con las firmas reales de
`localforge/harness/context.py` y `localforge/models.py` del paso 2.

El escenario imita lo que arma `AgentHarness.run()` en `harness/loop.py`:
messages[0] es la task como mensaje "user", y despues alternan pedidos del
assistant (con tool_calls) y observaciones (rol "tool").
"""

from __future__ import annotations

from localforge.harness.context import (
    BuiltContext,
    ContextBudget,
    ContextBuilder,
    TokenEstimator,
)
from localforge.models import AgentMessage, ToolCall, ToolDefinition

# Lo que _compact() escribe en lugar de una observacion. Es el prefijo de
# _COMPACTED_TEMPLATE; se repite aca a proposito para no importar un privado.
MARKER = "[observacion compactada"
PREVIEW = 70


# ---------------------------------------------------------------------------
# Datos del escenario
# ---------------------------------------------------------------------------

SYSTEM = "Sos un coding agent. Usa las tools para leer el repo antes de responder."
TASK = "Explica como funciona la autenticacion del proyecto."

DEFINITIONS = [
    ToolDefinition(
        name="read_file",
        description="Lee un archivo del repositorio.",
        input_schema={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    )
]

BUDGET = ContextBudget(limit=3000, reserve_output=500, keep_recent_messages=4)


def build_state() -> list[AgentMessage]:
    """Los 10 mensajes del state, como los dejaria el loop despues de 4 lecturas."""
    msgs = [AgentMessage(role="user", content=TASK)]
    reads = [
        ("config.py", "A", "Primero miro la configuracion."),
        ("main.py", "B", "Ahora el punto de entrada."),
        ("auth.py", "C", "Veo el modulo de autenticacion."),
        ("database.py", "D", "Falta ver como se guardan los usuarios."),
    ]
    for n, (path, fill, thought) in enumerate(reads, start=1):
        call = ToolCall(id=f"call_{n}", name="read_file", arguments={"path": path})
        msgs.append(AgentMessage(role="assistant", content=thought, tool_calls=[call]))
        msgs.append(
            AgentMessage(
                role="tool",
                content=fill * 3600,
                tool_call_id=call.id,
                tool_name="read_file",
            )
        )
    msgs.append(
        AgentMessage(
            role="assistant",
            content="Con config, main, auth y database ya puedo explicar el flujo de login.",
        )
    )
    return msgs


# ---------------------------------------------------------------------------
# Helpers de impresion
# ---------------------------------------------------------------------------


def title(text: str) -> None:
    print(f"\n{'=' * 78}\n=== {text}\n{'=' * 78}")


def preview(content: str | None) -> str:
    if not content:
        return "(vacio)"
    if len(content) <= PREVIEW:
        return content
    return f"{content[:PREVIEW]}... (+{len(content) - PREVIEW} chars)"


def msg_tokens(est: TokenEstimator, m: AgentMessage) -> int:
    """Lo que cuesta UN mensaje para _compact(): contenido + tool_calls + overhead."""
    return est.estimate_messages([m])


def row(i: int, m: AgentMessage, est: TokenEstimator, extra: str = "") -> str:
    return (
        f"{i + 1:02} | {m.role:9} | {(m.tool_name or '-'):9} | "
        f"{len(m.content or ''):>6} chars | {msg_tokens(est, m):>5} tok{extra}"
    )


# ---------------------------------------------------------------------------
# La demo
# ---------------------------------------------------------------------------


def main() -> None:
    est = TokenEstimator()
    messages = build_state()
    k = BUDGET.keep_recent_messages
    protected_from = max(0, len(messages) - k)  # la misma cuenta que _compact()

    # Fotos para comparar al final: si build() mutara el state, se veria aca.
    original_contents = [m.content for m in messages]
    original_ids = [id(m) for m in messages]

    # -- 1. STATE --------------------------------------------------------------
    title("1. STATE ORIGINAL (lo que guarda el loop)")
    print(f"   chars_per_token = {est.chars_per_token} (sin calibrar)\n")
    print("nn | role      | tool_name |      chars |   tokens | protegido")
    print("-" * 78)
    for i, m in enumerate(messages):
        prot = "  SI (ultimos k)" if i >= protected_from else "  no"
        print(row(i, m, est, prot))
    state_tokens = est.estimate_messages(messages)
    print("-" * 78)
    print(f"total messages del state: {state_tokens} tok")

    # -- 2. Mensajes protegidos -------------------------------------------------
    title("2. MENSAJES PROTEGIDOS")
    print(f"len(messages)         = {len(messages)}")
    print(f"keep_recent_messages  = {k}")
    print(f"protected_from        = len(messages) - keep_recent_messages = {protected_from}")
    print(f"indices protegidos    : {', '.join(str(i) for i in range(protected_from, len(messages)))}")
    print(f"mensajes protegidos   : {', '.join(str(i + 1) for i in range(protected_from, len(messages)))}")
    print("candidatos a compactar (role=tool, fuera de la zona protegida):")
    for i in range(protected_from):
        if messages[i].role == "tool":
            print(f"   message {i + 1:02} (indice {i}) — {messages[i].tool_name}")

    # -- 3. Presupuesto ---------------------------------------------------------
    title("3. CALCULO DE PRESUPUESTO (lo mismo que hace build())")
    sys_tok = est.estimate(SYSTEM)
    tools_tok = est.estimate_tools(DEFINITIONS)
    task_tok = est.estimate(TASK)
    fixed = sys_tok + tools_tok + task_tok
    room = BUDGET.available - fixed
    print(f"context limit          = {BUDGET.limit:>6}")
    print(f"reserve_output         = {BUDGET.reserve_output:>6}")
    print(f"available para input   = {BUDGET.available:>6}   (limit - reserve_output)")
    print()
    print(f"tokens de system       = {sys_tok:>6}")
    print(f"tokens de tools defs   = {tools_tok:>6}")
    print(f"tokens de task         = {task_tok:>6}")
    print(f"fixed                  = {fixed:>6}   (system + tools + task)")
    print(f"room para messages     = {room:>6}   (available - fixed)")
    print()
    print(f"messages del state     = {state_tokens:>6}")
    verdict = "NO entran -> _compact() va a trabajar" if state_tokens > room else "entran sin compactar"
    print(f"exceso                 = {state_tokens - room:>6}   -> {verdict}")

    # -- 4. build() --------------------------------------------------------------
    title("4. ContextBuilder.build()")
    builder = ContextBuilder(BUDGET, est)
    built: BuiltContext = builder.build(
        system=SYSTEM, task=TASK, messages=messages, definitions=DEFINITIONS
    )
    print("built = builder.build(system=..., task=..., messages=messages, definitions=...)")
    print(f"type(built)             = {type(built).__name__}")
    print(f"len(built.messages)     = {len(built.messages)}")
    print(f"built.messages is messages ? {built.messages is messages}")

    # -- 5. CONTEXT --------------------------------------------------------------
    title("5. CONTEXT PROYECTADO (built.messages, lo que viaja al modelo)")
    print("nn | role      | tool_name |      chars |   tokens | preview")
    print("-" * 78)
    for i, m in enumerate(built.messages):
        print(row(i, m, est) + f" | {preview(m.content)}")
    context_tokens = est.estimate_messages(built.messages)
    print("-" * 78)
    print(f"total messages del context: {context_tokens} tok (room era {room})")

    # -- 6. Comparacion ----------------------------------------------------------
    title("6. COMPARACION STATE VS CONTEXT")
    compacted_idx: list[int] = []
    for i, (s, c) in enumerate(zip(messages, built.messages)):
        was_compacted = s.content != c.content and (c.content or "").startswith(MARKER)
        if was_compacted:
            compacted_idx.append(i)
        same_obj = s is c
        print(f"message {i + 1}  (indice {i}){'  <-- protegido' if i >= protected_from else ''}")
        print(f"   STATE  : role={s.role:9} chars={len(s.content or ''):>5}  tok={msg_tokens(est, s):>5}")
        print(
            f"   CONTEXT: role={c.role:9} chars={len(c.content or ''):>5}  tok={msg_tokens(est, c):>5}"
            f"  COMPACTED={was_compacted}  mismo_objeto={same_obj}"
        )

    # -- 7. Orden de compactacion -------------------------------------------------
    title("7. ORDEN DE COMPACTACION (inferido comparando messages vs built.messages)")
    if not compacted_idx:
        print("ninguna observacion fue compactada")
    for n, i in enumerate(compacted_idx, start=1):
        s, c = messages[i], built.messages[i]
        saved = est.estimate(s.content) - est.estimate(c.content)
        print(f"{n}. message {i + 1} — {s.tool_name} — {len(s.content)} chars -> {len(c.content)} chars (-{saved} tok)")
    skipped = [
        i for i in range(protected_from)
        if messages[i].role == "tool" and i not in compacted_idx
    ]
    if skipped:
        print("\nobservaciones compactables que NO se tocaron (ya entraba antes de llegar):")
        for i in skipped:
            print(f"   message {i + 1} — {messages[i].tool_name}")
    print("\ncontenido completo de un marcador:")
    if compacted_idx:
        print(f"   {built.messages[compacted_idx[0]].content}")

    # -- 8. Breakdown ------------------------------------------------------------
    title("8. ContextBreakdown")
    b = built.breakdown
    print(b.table())
    print()
    print(f"estimated_input    = {built.estimated_input}")
    print(f"available          = {b.available}")
    print(f"fits               = {b.fits}")
    print(f"compacted_messages = {b.compacted_messages}")
    print(f"recovered_tokens   = {b.recovered_tokens}")
    biggest = max((l for l in b.layers if l.present), key=lambda l: l.tokens)
    print(f"capa mas grande    = {biggest.name} ({biggest.tokens} tok)")

    # -- 9. STATE != CONTEXT -------------------------------------------------------
    title("9. STATE != CONTEXT")
    first = compacted_idx[0] if compacted_idx else 2
    print(f"len(messages[{first}].content)       = {len(messages[first].content)}")
    print(f"len(built.messages[{first}].content) = {len(built.messages[first].content)}")
    print(f"messages[{first}].content[:20]       = {messages[first].content[:20]!r}")
    print(f"built.messages[{first}].content[:20] = {built.messages[first].content[:20]!r}")
    print(f"state  : {state_tokens} tok   context: {context_tokens} tok")
    print()

    checks = []

    # 1. el state original no fue modificado
    assert [m.content for m in messages] == original_contents
    assert [id(m) for m in messages] == original_ids
    assert all(len(messages[i].content) == 3600 for i in (2, 4, 6, 8))
    checks.append("el state original no fue modificado (contenidos e identidades iguales)")

    # 2. al menos una observacion vieja fue compactada
    assert compacted_idx, "no se compacto ninguna observacion"
    assert all(i < protected_from and messages[i].role == "tool" for i in compacted_idx)
    checks.append(f"se compactaron {len(compacted_idx)} observaciones viejas: indices {compacted_idx}")

    # 2b. de la mas vieja a la mas nueva: lo compactado es un prefijo de las candidatas
    candidates = [i for i in range(protected_from) if messages[i].role == "tool"]
    assert compacted_idx == candidates[: len(compacted_idx)]
    checks.append("el orden fue de la mas vieja a la mas nueva (sin saltos)")

    # 3. los ultimos keep_recent_messages permanecieron intactos
    for i in range(protected_from, len(messages)):
        assert built.messages[i].content == messages[i].content
        assert built.messages[i] is messages[i]
    checks.append(f"los ultimos {k} mensajes (indices {protected_from}..{len(messages) - 1}) quedaron intactos")

    # 4. el breakdown registra la compactacion
    assert built.breakdown.compacted_messages > 0
    assert built.breakdown.compacted_messages == len(compacted_idx)
    checks.append(f"breakdown.compacted_messages = {built.breakdown.compacted_messages} > 0")

    # 5. el contexto pesa menos que el state
    assert context_tokens < state_tokens
    checks.append(f"context ({context_tokens} tok) < state ({state_tokens} tok)")

    # 6. con este presupuesto, entra
    assert built.breakdown.fits is True
    checks.append(f"breakdown.fits is True ({built.estimated_input} <= {b.available})")

    for c in checks:
        print(f"  OK  {c}")
    print("\ntodas las assertions pasaron.")


if __name__ == "__main__":
    main()
