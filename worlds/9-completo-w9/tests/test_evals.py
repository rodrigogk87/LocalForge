"""Tests de los evals (Fase 7).

Sutileza que vale la pena nombrar: estos tests testean el **medidor**, no al
agente. Un eval roto que reporta 100% es peor que no tener eval, asi que el
medidor necesita sus propios tests -- con outcomes armados a mano, donde se sabe
exactamente cual tiene que ser el veredicto.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from localforge.evals import (
    CitesFileAndLine,
    EvalReport,
    GoldenTask,
    Mentions,
    NoHedging,
    Succeeded,
    TaskResult,
    UsedTool,
    WithinBudget,
    compare,
    localforge_suite,
    run_suite,
)
from localforge.models import AgentOutcome, AgentStatus, AgentTask, FailureReason


def outcome(**kw) -> AgentOutcome:
    defaults = dict(
        task_id=uuid4(),
        status=AgentStatus.COMPLETED,
        output="",
        turns=3,
        input_tokens=1000,
        output_tokens=200,
        duration_ms=5000,
        trajectory=[],
    )
    return AgentOutcome(**{**defaults, **kw})


# --- checks individuales ----------------------------------------------------


def test_succeeded() -> None:
    assert Succeeded().run(outcome()).passed
    fallo = Succeeded().run(outcome(status=AgentStatus.FAILED, reason=FailureReason.MAX_TURNS))
    assert not fallo.passed
    assert "max_turns" in fallo.detail


def test_mentions_es_case_insensitive() -> None:
    check = Mentions(("safe_path", "fs.py"))
    assert check.run(outcome(output="Se valida en FS.PY con SAFE_PATH")).passed


def test_mentions_dice_que_falto() -> None:
    r = Mentions(("safe_path", "fs.py")).run(outcome(output="se valida en fs.py"))
    assert not r.passed
    assert "safe_path" in r.detail


def test_used_tool_cuenta_repeticiones() -> None:
    o = outcome(trajectory=["read_file", "read_file", "list_files"])
    assert UsedTool("read_file", at_least=2).run(o).passed
    assert not UsedTool("read_file", at_least=3).run(o).passed
    assert not UsedTool("search_code").run(o).passed


def test_no_hedging() -> None:
    assert NoHedging().run(outcome(output="fs.py:31 valida la ruta")).passed
    r = NoHedging().run(outcome(output="posiblemente sea cli.py"))
    assert not r.passed and "posiblemente" in r.detail


@pytest.mark.parametrize(
    "texto,espera",
    [
        ("ver fs.py:31", True),
        ("en fs.py linea 31", True),
        ("en tools/fs.py (31)", True),
        ("esta en fs.py", False),
        ("son 31 lineas", False),
    ],
)
def test_cites_file_and_line(texto: str, espera: bool) -> None:
    assert CitesFileAndLine().run(outcome(output=texto)).passed is espera


def test_within_budget_reporta_todo_lo_que_se_paso() -> None:
    o = outcome(turns=15, input_tokens=50_000, output_tokens=0, duration_ms=120_000)
    r = WithinBudget(max_turns=10, max_tokens=20_000, max_seconds=60).run(o)
    assert not r.passed
    assert "turnos" in r.detail and "tokens" in r.detail and "s >" in r.detail


def test_within_budget_pasa_cuando_entra() -> None:
    assert WithinBudget(max_turns=10, max_tokens=5_000).run(outcome()).passed


# --- reporte ----------------------------------------------------------------


def result(task_id: str, *, ok: bool, **kw) -> TaskResult:
    o = outcome(**kw)
    checks = [Succeeded().run(o)] if ok or "status" in kw else [Mentions(("x",)).run(o)]
    return TaskResult(task_id=task_id, tags=(), outcome=o, checks=checks, wall_seconds=1.0)


def test_pass_rate() -> None:
    rep = EvalReport(results=[result("a", ok=True), result("b", ok=False)])
    assert rep.total == 2 and rep.passed == 1 and rep.pass_rate == 50.0


def test_taxonomia_agrupa_por_failure_reason() -> None:
    rep = EvalReport(
        results=[
            result("a", ok=False, status=AgentStatus.FAILED, reason=FailureReason.MAX_TURNS),
            result("b", ok=False, status=AgentStatus.FAILED, reason=FailureReason.MAX_TURNS),
            result("c", ok=False, status=AgentStatus.FAILED, reason=FailureReason.LOOP_DETECTED),
        ]
    )
    tax = rep.failure_taxonomy()
    assert tax["max_turns"] == 2 and tax["loop_detected"] == 1


def test_un_fallo_de_calidad_no_es_un_fallo_de_ejecucion() -> None:
    """Termino bien pero no paso los checks: es otra categoria."""
    rep = EvalReport(results=[result("a", ok=False)])
    assert rep.failure_taxonomy()["calidad"] == 1


def test_el_reporte_dice_que_check_falla_mas() -> None:
    rep = EvalReport(results=[result("a", ok=False), result("b", ok=False)])
    assert rep.failed_check_counts()["menciona lo esperado"] == 2


def test_costo_y_latencia() -> None:
    rep = EvalReport(results=[result("a", ok=True), result("b", ok=True, input_tokens=3000)])
    cost = rep.cost()
    assert cost["tokens_total"] == 1200 + 3200
    assert cost["tokens_max"] == 3200


def test_render_no_explota_y_dice_lo_esencial() -> None:
    rep = EvalReport(
        label="con search_code",
        results=[result("a", ok=True), result("b", ok=False, status=AgentStatus.FAILED, reason=FailureReason.WALL_CLOCK)],
    )
    texto = rep.render()
    assert "con search_code" in texto
    assert "1/2" in texto
    assert "wall_clock" in texto


def test_render_de_un_reporte_vacio() -> None:
    assert "0/0" in EvalReport().render()


# --- runner -----------------------------------------------------------------


async def test_run_suite_corre_todo_y_mide(tmp_path) -> None:  # noqa: ANN001
    tasks = [
        GoldenTask(
            id="t1",
            objective="x",
            repo_path=str(tmp_path),
            checks=(Succeeded(), Mentions(("safe_path",))),
        ),
        GoldenTask(id="t2", objective="y", repo_path=str(tmp_path), checks=(Succeeded(),)),
    ]

    async def run(task: AgentTask) -> AgentOutcome:
        return outcome(output="esta en safe_path" if "x" in task.objective else "otra cosa")

    rep = await run_suite(run, tasks, label="prueba")
    assert rep.total == 2 and rep.passed == 2
    assert rep.label == "prueba"


async def test_run_suite_llama_al_callback_por_tarea(tmp_path) -> None:  # noqa: ANN001
    vistos: list[str] = []
    tasks = [GoldenTask(id=f"t{i}", objective="x", repo_path=str(tmp_path), checks=(Succeeded(),)) for i in range(3)]

    async def run(task: AgentTask) -> AgentOutcome:
        return outcome()

    await run_suite(run, tasks, on_task=lambda r: vistos.append(r.task_id))
    assert vistos == ["t0", "t1", "t2"]


# --- comparacion ------------------------------------------------------------


def test_compare_detecta_mejoras_y_regresiones() -> None:
    a = EvalReport(
        label="antes",
        results=[
            result("mejora", ok=False),
            result("regresion", ok=True),
            result("igual", ok=True),
        ],
    )
    b = EvalReport(
        label="despues",
        results=[
            result("mejora", ok=True),
            result("regresion", ok=False),
            result("igual", ok=True),
        ],
    )
    texto = compare(a, b)
    assert "1 mejora(s), 1 regresion(es)" in texto
    assert "OJO" in texto  # avisa que una regresion se esconde detras del promedio


def test_compare_muestra_el_delta_de_tokens() -> None:
    a = EvalReport(results=[result("t", ok=True)])
    b = EvalReport(results=[result("t", ok=True, input_tokens=2000)])
    assert "+1000" in compare(a, b)


# --- el dataset -------------------------------------------------------------


def test_la_suite_incluye_la_pregunta_que_fallo_en_el_m1() -> None:
    suite = localforge_suite("/tmp/repo")
    regresion = next(t for t in suite if t.id == "donde-se-valida-la-ruta")
    # Exige mencionar safe_path Y haber usado search_code: el fallo original era
    # justamente responder sin encontrarlo.
    nombres = {type(c).__name__ for c in regresion.checks}
    assert "Mentions" in nombres and "UsedTool" in nombres


def test_todas_las_tareas_tienen_id_unico_y_checks() -> None:
    suite = localforge_suite("/tmp/repo")
    assert len({t.id for t in suite}) == len(suite)
    for t in suite:
        assert t.checks, t.id


def test_la_tarea_de_seguridad_no_exige_exito() -> None:
    """Pedirle el .env: lo correcto es que NO pueda, asi que no se exige Succeeded."""
    tarea = next(t for t in localforge_suite("/tmp/repo") if t.id == "secreto-no-se-lee")
    assert not any(isinstance(c, Succeeded) for c in tarea.checks)
