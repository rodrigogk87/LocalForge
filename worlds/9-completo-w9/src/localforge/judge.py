"""Model-as-judge (W7-C45): medir lo que no se puede medir con una regla.

Los checks deterministas de `evals.py` contestan "¿menciono `safe_path`?". No
pueden contestar "¿la explicacion de COMO valida la ruta es correcta?". Para eso
hace falta leer la respuesta, y la unica forma automatica de leer es otro modelo.

El problema es que el juez es un modelo, con los sesgos de un modelo. Este
modulo existe tanto para usar un juez como para **no creerle de mas**:

1. **Sesgo de posicion.** Comparando A contra B, los jueces prefieren la que va
   primero (o la segunda, segun el modelo). `compare()` juzga DOS veces con el
   orden invertido y solo declara ganador si las dos veces gana la misma. Si no,
   es empate y el reporte dice por que.

2. **Sesgo de verbosidad.** Los jueces premian lo largo. La rubrica se lo dice
   explicitamente, y el puntaje se pide sobre criterios concretos, no sobre
   "calidad general".

3. **Autocomplacencia.** Un modelo juzgando sus propias respuestas es mas
   generoso. Se puede usar otro modelo como juez (`LOCALFORGE_JUDGE_MODEL`), y si
   es el mismo, el resultado lo marca: `same_model=True` se ve en el reporte.

4. **Un juez ilegible no aprueba.** Si la respuesta del juez no trae el JSON
   pedido, el check FALLA con "juez ilegible". El default optimista seria el
   mismo error que `ToolResult.success` sin default: cada olvido, un falso
   positivo.

Y la regla de orden: el juez corre DESPUES de los checks deterministas y no los
reemplaza. Si una respuesta no menciona el archivo correcto, no hace falta que
nadie la lea para saber que esta mal.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from localforge.models import AgentMessage
from localforge.providers.base import ModelProvider, ProviderError

# Puntaje minimo para aprobar, sobre 5. 4 y no 3: un 3 es "mas o menos", y un
# eval que aprueba "mas o menos" no distingue nada.
PASS_SCORE = 4

# El juez lee la respuesta, no la tesis. Mas que esto se corta y se avisa.
MAX_ANSWER_CHARS = 6_000

JUDGE_SYSTEM = (
    "Sos un evaluador estricto de respuestas sobre codigo. Evaluas contra una "
    "rubrica concreta. La longitud NO suma puntos: una respuesta corta que cumple "
    "la rubrica vale mas que una larga que la rodea. No inventes criterios que la "
    "rubrica no pide. Respondes SOLO con JSON."
)

SCORE_PROMPT = """PREGUNTA:
{question}

RUBRICA (lo que una buena respuesta tiene que tener):
{rubric}

RESPUESTA A EVALUAR:
<<<
{answer}
>>>

Puntua de 1 a 5 segun cuanto de la rubrica cumple la respuesta (5 = todo, 1 = nada
o incorrecto). Si afirma algo falso sobre el codigo, maximo 2.
Responde solo: {{"score": <1-5>, "reason": "<una frase: que cumple y que falta>"}}"""

PAIR_PROMPT = """PREGUNTA:
{question}

RUBRICA:
{rubric}

RESPUESTA 1:
<<<
{first}
>>>

RESPUESTA 2:
<<<
{second}
>>>

¿Cual cumple mejor la rubrica? La longitud no cuenta.
Responde solo: {{"winner": 1 | 2 | 0, "reason": "<una frase>"}}  (0 = empate)"""

_JSON = re.compile(r"\{.*?\}", re.S)


@dataclass(frozen=True)
class JudgeResult:
    passed: bool
    score: int | None
    reason: str
    same_model: bool = False

    @property
    def detail(self) -> str:
        nota = f"{self.score}/5" if self.score is not None else "sin puntaje"
        extra = " · juez = mismo modelo que el agente" if self.same_model else ""
        return f"{nota}: {self.reason}{extra}"


@dataclass(frozen=True)
class PairResult:
    winner: str  # "a", "b" o "empate"
    reason: str
    # Las dos lecturas, para que el reporte muestre si hubo sesgo de posicion.
    first_pass: str
    second_pass: str

    @property
    def position_bias(self) -> bool:
        return self.first_pass != self.second_pass and "empate" not in (self.first_pass, self.second_pass)


def _parse(text: str | None) -> dict | None:
    for m in _JSON.finditer(text or ""):
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data
    return None


def _clip(answer: str) -> str:
    if len(answer) <= MAX_ANSWER_CHARS:
        return answer
    return answer[:MAX_ANSWER_CHARS] + f"\n[...respuesta cortada en {MAX_ANSWER_CHARS} caracteres]"


class LLMJudge:
    def __init__(self, provider: ModelProvider, *, agent_model: str = "") -> None:
        self.provider = provider
        self.same_model = bool(agent_model) and agent_model == getattr(provider, "model", "")

    async def _ask(self, prompt: str) -> str | None:
        response = await self.provider.complete(
            [AgentMessage(role="user", content=prompt)], None, system=JUDGE_SYSTEM
        )
        return response.content

    async def score(self, question: str, answer: str, rubric: str) -> JudgeResult:
        if not answer.strip():
            return JudgeResult(False, None, "la respuesta esta vacia", self.same_model)
        try:
            raw = await self._ask(SCORE_PROMPT.format(question=question, rubric=rubric, answer=_clip(answer)))
        except ProviderError as exc:
            return JudgeResult(False, None, f"el juez fallo: {exc}", self.same_model)
        data = _parse(raw)
        try:
            score = int(data["score"]) if data else None
        except (KeyError, TypeError, ValueError):
            score = None
        if score is None or not 1 <= score <= 5:
            return JudgeResult(
                False, None, f"juez ilegible: {(raw or '')[:120]!r}", self.same_model
            )
        reason = str(data.get("reason", "")).strip() or "(sin motivo)"
        return JudgeResult(score >= PASS_SCORE, score, reason, self.same_model)

    async def compare(self, question: str, a: str, b: str, rubric: str) -> PairResult:
        """Juzga dos veces con el orden invertido. Ganador solo si coinciden."""

        async def once(first: str, second: str, names: tuple[str, str]) -> tuple[str, str]:
            raw = await self._ask(
                PAIR_PROMPT.format(question=question, rubric=rubric, first=_clip(first), second=_clip(second))
            )
            data = _parse(raw) or {}
            w = str(data.get("winner", "0")).strip()
            pick = {"1": names[0], "2": names[1]}.get(w, "empate")
            return pick, str(data.get("reason", "")).strip()

        p1, r1 = await once(a, b, ("a", "b"))
        p2, r2 = await once(b, a, ("b", "a"))
        if p1 == p2:
            return PairResult(p1, r1 or r2, p1, p2)
        return PairResult(
            "empate",
            f"el juez cambio de opinion al invertir el orden ({p1} -> {p2}): sesgo de posicion",
            p1,
            p2,
        )


__all__ = ["LLMJudge", "JudgeResult", "PairResult", "PASS_SCORE", "SCORE_PROMPT", "PAIR_PROMPT"]
