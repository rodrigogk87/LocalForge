"""Cuantos tokens cuesta un texto, sin tokenizer.

Primera de las cuatro piezas del Mundo 2. Esta primero porque las otras tres
dependen de ella: no se puede presupuestar lo que no se mide.
"""

from __future__ import annotations

from typing import Iterable, Sequence

from localforge.models import AgentMessage, ToolDefinition

# Un tokenizer real (tiktoken, el de qwen) seria exacto pero agrega una
# dependencia pesada y distinta por modelo. Y no hace falta: para decidir
# "¿entra o no?" alcanza una estimacion con error conocido y sesgo controlado.
#
# 3.6 caracteres por token es el punto de partida para codigo y prosa tecnica
# mezclados. El codigo tokeniza peor que la prosa (mas simbolos, mas
# identificadores raros), asi que el numero es mas bajo que el 4.0 que se cita
# para ingles corriente.
DEFAULT_CHARS_PER_TOKEN = 3.6

# Limites de cordura para la calibracion. Si el ratio se va afuera de esto, o
# el texto era rarisimo o hay un bug; en cualquier caso no le creemos.
_MIN_RATIO = 1.5
_MAX_RATIO = 8.0

# Peso de cada observacion nueva en la media movil. Bajo a proposito: preferimos
# converger despacio a saltar por un turno atipico.
_EMA_ALPHA = 0.25


class TokenEstimator:
    """Estima tokens por longitud, y se CALIBRA con los conteos reales.

    El truco que hace esto honesto: despues de cada llamada, Ollama devuelve
    `prompt_eval_count`, que es el conteo real de tokens de input. Comparamos
    nuestra estimacion contra ese numero y ajustamos el ratio.

    O sea que el estimador arranca siendo una heuristica y se convierte, a los
    pocos turnos, en una medicion calibrada contra el tokenizer de ESTE modelo.
    Sin dependencias y sin adivinar cual tokenizer usa.
    """

    def __init__(self, chars_per_token: float = DEFAULT_CHARS_PER_TOKEN) -> None:
        self.chars_per_token = chars_per_token
        self.samples = 0
        self._last_error_pct: float | None = None

    def estimate(self, text: str | None) -> int:
        if not text:
            return 0
        # El +1 evita que un texto cortito estime 0 tokens: todo texto cuesta.
        return int(len(text) / self.chars_per_token) + 1

    def estimate_messages(self, messages: Iterable[AgentMessage]) -> int:
        total = 0
        for m in messages:
            total += self.estimate(m.content)
            for call in m.tool_calls:
                # Los argumentos viajan serializados: cuestan tokens igual.
                total += self.estimate(call.name) + self.estimate(str(call.arguments))
            # Todo mensaje paga un overhead de estructura (rol, delimitadores).
            total += _MESSAGE_OVERHEAD
        return total

    def estimate_tools(self, definitions: Sequence[ToolDefinition]) -> int:
        total = 0
        for d in definitions:
            total += self.estimate(d.name) + self.estimate(d.description)
            total += self.estimate(str(d.input_schema))
        return total

    def observe(self, estimated: int, actual: int) -> None:
        """Recalibra el ratio con un conteo real.

        `actual` es `prompt_eval_count` de Ollama: los tokens que el modelo
        efectivamente leyo. Si estimamos de menos, el ratio real de caracteres
        por token es mas chico que el nuestro, y al revez.
        """
        if estimated <= 0 or actual <= 0:
            return
        implied = self.chars_per_token * (estimated / actual)
        if not (_MIN_RATIO <= implied <= _MAX_RATIO):
            return  # fuera de rango: no le creemos a esta muestra
        self._last_error_pct = (estimated - actual) / actual * 100
        self.chars_per_token += _EMA_ALPHA * (implied - self.chars_per_token)
        self.samples += 1

    @property
    def last_error_pct(self) -> float | None:
        """Error de la ultima estimacion, en porcentaje. Positivo = estimamos de mas."""
        return self._last_error_pct

    @property
    def calibrated(self) -> bool:
        return self.samples > 0


# Cada mensaje agrega delimitadores de rol al prompt. El numero exacto depende
# del template del modelo; 4 es la aproximacion habitual y el error que mete es
# irrelevante frente al del cuerpo del mensaje.
_MESSAGE_OVERHEAD = 4


__all__ = ["TokenEstimator", "DEFAULT_CHARS_PER_TOKEN"]
