"""Tests de context engineering (Fase 2).

Lo que se testea aca no es "el contexto se armo": es que el presupuesto se
respete, que la compactacion no borre lo que no debe, y que nunca compacte en
silencio. Son las tres propiedades que hacen que un agente de muchos turnos no
se degrade.
"""

from __future__ import annotations

from localforge.harness.context import (
    ContextBudget,
    ContextBuilder,
    TokenEstimator,
)
from localforge.models import AgentMessage, ToolCall, ToolDefinition

SYSTEM = "Sos un asistente de ingenieria. " * 10
TASK = "explicame la arquitectura de este repo"


def tool_defs() -> list[ToolDefinition]:
    return [
        ToolDefinition(
            name="read_file",
            description="Lee un archivo del repo y lo devuelve numerado por lineas.",
            input_schema={"type": "object", "properties": {"path": {"type": "string"}}},
        ),
        ToolDefinition(
            name="list_files",
            description="Lista los archivos del repo en forma de arbol.",
            input_schema={"type": "object", "properties": {"path": {"type": "string"}}},
        ),
    ]


def conversation(observations: int, obs_chars: int = 4_000) -> list[AgentMessage]:
    """Una conversacion realista: task, y despues pares assistant/tool."""
    msgs: list[AgentMessage] = [AgentMessage(role="user", content=TASK)]
    for i in range(observations):
        msgs.append(
            AgentMessage(
                role="assistant",
                content=f"voy a leer el archivo {i}",
                tool_calls=[ToolCall(id=f"c{i}", name="read_file", arguments={"path": f"f{i}.py"})],
            )
        )
        msgs.append(
            AgentMessage(
                role="tool",
                content="x" * obs_chars,
                tool_call_id=f"c{i}",
                tool_name="read_file",
            )
        )
    return msgs


def builder(limit: int, **kw) -> ContextBuilder:
    return ContextBuilder(ContextBudget(limit=limit, **kw))


# --- estimacion -------------------------------------------------------------


def test_estimate_crece_con_el_texto() -> None:
    est = TokenEstimator()
    assert est.estimate("") == 0
    assert est.estimate("hola") >= 1
    assert est.estimate("x" * 4_000) > est.estimate("x" * 400)


def test_calibracion_acerca_el_ratio_al_real() -> None:
    est = TokenEstimator(chars_per_token=3.6)
    texto = "x" * 3_600  # nuestra estimacion: ~1000 tokens
    estimado = est.estimate(texto)

    # El modelo dice que en realidad fueron 1800: tokenizamos de menos, o sea
    # que el ratio real de chars/token es MAS CHICO que 3.6.
    for _ in range(20):
        est.observe(estimado, 1_800)

    assert est.calibrated
    assert est.chars_per_token < 3.6
    # Tras calibrar, la estimacion del mismo texto tiene que subir.
    assert est.estimate(texto) > estimado


def test_calibracion_ignora_muestras_absurdas() -> None:
    est = TokenEstimator(chars_per_token=3.6)
    antes = est.chars_per_token
    est.observe(1_000, 1)       # implicaria 3600 chars/token
    est.observe(0, 500)         # estimacion invalida
    est.observe(1_000, 0)       # conteo invalido
    assert est.chars_per_token == antes
    assert not est.calibrated


# --- anatomia ---------------------------------------------------------------


def test_breakdown_reparte_en_capas() -> None:
    built = builder(32_768).build(
        system=SYSTEM, task=TASK, messages=conversation(2), definitions=tool_defs()
    )
    bd = built.breakdown

    assert bd.layer("instructions").tokens > 0
    assert bd.layer("tools").tokens > 0
    assert bd.layer("task").tokens > 0
    assert bd.layer("observations").tokens > 0
    # Las observaciones son la capa grande: es la razon de ser del compactador.
    assert bd.layer("observations").tokens > bd.layer("conversation").tokens
    assert bd.total == sum(l.tokens for l in bd.layers)


def test_las_capas_que_no_existen_se_reportan_ausentes() -> None:
    built = builder(32_768).build(
        system=SYSTEM, task=TASK, messages=conversation(1), definitions=tool_defs()
    )
    ausentes = {l.name for l in built.breakdown.layers if not l.present}
    assert ausentes == {"environment", "skills", "memory", "retrieved"}


def test_solo_las_observaciones_son_compactables() -> None:
    built = builder(32_768).build(
        system=SYSTEM, task=TASK, messages=conversation(1), definitions=tool_defs()
    )
    compactables = {l.name for l in built.breakdown.layers if l.compactable}
    assert compactables == {"observations"}


# --- presupuesto ------------------------------------------------------------


def test_reserva_lugar_para_la_salida() -> None:
    b = ContextBudget(limit=10_000, reserve_output=4_000)
    assert b.available == 6_000


def test_contexto_holgado_no_se_toca() -> None:
    msgs = conversation(2)
    built = builder(32_768).build(
        system=SYSTEM, task=TASK, messages=msgs, definitions=tool_defs()
    )
    assert built.breakdown.compacted_messages == 0
    assert [m.content for m in built.messages] == [m.content for m in msgs]
    assert built.breakdown.fits


# --- compactacion -----------------------------------------------------------


def test_compacta_cuando_no_entra() -> None:
    # 10 observaciones de 4000 chars ~ 11k tokens, contra 4000 disponibles.
    built = builder(8_000, reserve_output=4_000).build(
        system=SYSTEM, task=TASK, messages=conversation(10), definitions=tool_defs()
    )
    assert built.breakdown.compacted_messages > 0
    assert built.breakdown.recovered_tokens > 0


def test_la_compactacion_deja_marcador_nunca_en_silencio() -> None:
    built = builder(8_000, reserve_output=4_000).build(
        system=SYSTEM, task=TASK, messages=conversation(10), definitions=tool_defs()
    )
    marcadas = [m for m in built.messages if (m.content or "").startswith("[observacion compactada")]
    assert marcadas, "compacto sin dejar ninguna señal"
    # El marcador tiene que decir QUE tool era y COMO recuperarlo.
    assert "read_file" in marcadas[0].content
    assert "volve a pedir la tool" in marcadas[0].content


def test_nunca_compacta_la_task() -> None:
    msgs = conversation(12)
    built = builder(6_000, reserve_output=3_000).build(
        system=SYSTEM, task=TASK, messages=msgs, definitions=tool_defs()
    )
    assert built.messages[0].role == "user"
    assert built.messages[0].content == TASK


def test_nunca_compacta_el_razonamiento_del_assistant() -> None:
    built = builder(6_000, reserve_output=3_000).build(
        system=SYSTEM, task=TASK, messages=conversation(12), definitions=tool_defs()
    )
    for m in built.messages:
        if m.role == "assistant":
            assert not (m.content or "").startswith("[observacion compactada")


def test_protege_los_mensajes_recientes() -> None:
    built = ContextBuilder(
        ContextBudget(limit=6_000, reserve_output=3_000, keep_recent_messages=4)
    ).build(system=SYSTEM, task=TASK, messages=conversation(12), definitions=tool_defs())

    for m in built.messages[-4:]:
        assert not (m.content or "").startswith("[observacion compactada")


def test_compacta_de_la_mas_vieja_a_la_mas_nueva() -> None:
    built = builder(7_000, reserve_output=3_000).build(
        system=SYSTEM, task=TASK, messages=conversation(12), definitions=tool_defs()
    )
    obs = [m for m in built.messages if m.role == "tool"]
    compactadas = [(m.content or "").startswith("[observacion compactada") for m in obs]
    # Todas las compactadas tienen que venir antes que las intactas.
    assert compactadas == sorted(compactadas, reverse=True), compactadas


def test_compactar_es_idempotente() -> None:
    b = builder(7_000, reserve_output=3_000)
    once = b.build(system=SYSTEM, task=TASK, messages=conversation(12), definitions=tool_defs())
    twice = b.build(
        system=SYSTEM, task=TASK, messages=once.messages, definitions=tool_defs()
    )
    # Volver a compactar lo ya compactado no debe re-marcar ni inflar la cuenta.
    assert twice.breakdown.compacted_messages <= once.breakdown.compacted_messages
    for m in twice.messages:
        assert (m.content or "").count("[observacion compactada") <= 1


def test_la_tabla_menciona_las_capas_y_el_total() -> None:
    built = builder(32_768).build(
        system=SYSTEM, task=TASK, messages=conversation(3), definitions=tool_defs()
    )
    tabla = built.breakdown.table()
    assert "observations" in tabla
    assert "instructions" in tabla
    assert "contexto:" in tabla
