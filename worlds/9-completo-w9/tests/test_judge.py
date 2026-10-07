"""Model-as-judge (W7-C45, lo que el mundo dejo pendiente).

Se prueba sobre todo lo que lo hace confiable: que un juez ilegible NO aprueba,
que el sesgo de posicion se detecta invirtiendo el orden, y que el juez no
reemplaza a los checks deterministas.
"""

from __future__ import annotations

import pytest

from localforge.evals import GoldenTask, Mentions, Succeeded, localforge_suite, run_suite
from localforge.judge import PASS_SCORE, LLMJudge
from localforge.models import AgentOutcome, AgentStatus, ModelResponse, StopReason
from localforge.providers.base import ProviderError
from uuid import uuid4


class Juez:
    name = "juez"

    def __init__(self, respuestas: list[str | Exception], model: str = "juez-model") -> None:
        self.respuestas = list(respuestas)
        self.model = model
        self.prompts: list[str] = []
        self.tools_seen: list = []

    async def complete(self, messages, tools=None, *, system=None, max_tokens=None):  # noqa: ANN001
        self.prompts.append(messages[-1].content)
        self.tools_seen.append(tools)
        r = self.respuestas.pop(0)
        if isinstance(r, Exception):
            raise r
        return ModelResponse(content=r, stop_reason=StopReason.END_TURN)

    async def health(self):  # noqa: ANN201
        return {}

    async def aclose(self) -> None:
        return None


# --- score ------------------------------------------------------------------------


async def test_un_puntaje_alto_aprueba_y_uno_bajo_no() -> None:
    j = LLMJudge(Juez(['{"score": 5, "reason": "completo"}', '{"score": 3, "reason": "le falta"}']))
    alto = await j.score("q", "respuesta", "rubrica")
    bajo = await j.score("q", "respuesta", "rubrica")
    assert alto.passed and alto.score == 5
    assert not bajo.passed and bajo.score == 3 < PASS_SCORE


async def test_tolera_texto_alrededor_del_json() -> None:
    j = LLMJudge(Juez(['Claro! Aca va: {"score": 4, "reason": "ok"} espero que sirva']))
    assert (await j.score("q", "r", "rub")).passed


@pytest.mark.parametrize("raw", ["me parecio bien", '{"score": 9}', '{"reason": "sin score"}', '{"score": "alto"}'])
async def test_un_juez_ilegible_no_aprueba(raw: str) -> None:
    """El default optimista convertiria cada respuesta rara del juez en un pass."""
    res = await LLMJudge(Juez([raw])).score("q", "r", "rub")
    assert not res.passed and res.score is None
    assert "ilegible" in res.reason


async def test_un_juez_caido_no_aprueba() -> None:
    res = await LLMJudge(Juez([ProviderError("timeout")])).score("q", "r", "rub")
    assert not res.passed and "fallo" in res.reason


async def test_una_respuesta_vacia_ni_se_le_pregunta_al_juez() -> None:
    juez = Juez([])
    res = await LLMJudge(juez).score("q", "   ", "rub")
    assert not res.passed and juez.prompts == []


async def test_el_juez_no_recibe_tools_y_la_rubrica_desalienta_lo_largo() -> None:
    juez = Juez(['{"score": 4, "reason": "ok"}'])
    await LLMJudge(juez).score("q", "r", "rub")
    assert juez.tools_seen == [None]
    from localforge.judge import JUDGE_SYSTEM

    assert "longitud NO suma" in JUDGE_SYSTEM


async def test_se_marca_cuando_el_juez_es_el_mismo_modelo() -> None:
    res = await LLMJudge(Juez(['{"score": 5, "reason": "ok"}'], model="m"), agent_model="m").score("q", "r", "x")
    assert res.same_model and "mismo modelo" in res.detail


# --- comparacion con orden invertido -----------------------------------------------


async def test_gana_solo_si_gana_en_los_dos_ordenes() -> None:
    # Primera lectura: A primero, gana 1 (= A). Segunda: B primero, gana 2 (= A).
    j = LLMJudge(Juez(['{"winner": 1, "reason": "cita linea"}', '{"winner": 2, "reason": "cita linea"}']))
    res = await j.compare("q", "A", "B", "rub")
    assert res.winner == "a" and not res.position_bias


async def test_si_cambia_de_opinion_al_invertir_es_empate_por_sesgo() -> None:
    # Siempre elige la que va primero: sesgo de posicion puro.
    j = LLMJudge(Juez(['{"winner": 1}', '{"winner": 1}']))
    res = await j.compare("q", "A", "B", "rub")
    assert res.winner == "empate" and res.position_bias
    assert "sesgo de posicion" in res.reason


# --- integracion con run_suite ------------------------------------------------------


def outcome(text: str, ok: bool = True) -> AgentOutcome:
    return AgentOutcome(
        task_id=uuid4(), status=AgentStatus.COMPLETED if ok else AgentStatus.FAILED, output=text
    )


async def test_el_juez_corre_despues_de_los_checks_y_solo_si_pasaron() -> None:
    tareas = [
        GoldenTask("bien", "q1", ".", (Succeeded(), Mentions(("safe_path",))), rubric="r"),
        GoldenTask("mal", "q2", ".", (Succeeded(), Mentions(("safe_path",))), rubric="r"),
        GoldenTask("sin-rubrica", "q3", ".", (Succeeded(),)),
    ]
    salidas = {"q1": outcome("safe_path valida"), "q2": outcome("ni idea"), "q3": outcome("x")}
    juez = Juez(['{"score": 2, "reason": "explica mal el resolve"}'])

    async def run(task):  # noqa: ANN001, ANN202
        return salidas[task.objective]

    report = await run_suite(run, tareas, judge=LLMJudge(juez))
    assert len(juez.prompts) == 1, "el juez corrio donde no hacia falta"
    bien = report.results[0]
    assert not bien.passed and bien.failed_checks[0].name == "juez"
    assert "2/5" in bien.failed_checks[0].detail
    assert [c.name for c in report.results[1].checks] == ["termino bien", "menciona lo esperado"]
    assert report.results[2].passed


def test_las_tareas_que_piden_explicacion_traen_rubrica() -> None:
    suite = {t.id: t for t in localforge_suite(".")}
    assert suite["donde-se-valida-la-ruta"].rubric
    # La de seguridad no: lo correcto ahi es NO responder, y eso se mide exacto.
    assert not suite["secreto-no-se-lee"].rubric
