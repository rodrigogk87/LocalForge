"""Context Engineering (W2): la ventana es un presupuesto, no un buffer.

Tres modulos, en orden de dependencia:

  tokens.py   -- estimar, y calibrarse con el conteo real del modelo
  layers.py   -- que capa se come el contexto
  builder.py  -- el presupuesto y la compactacion
"""

from localforge.context.builder import BuiltContext, ContextBudget, ContextBuilder
from localforge.context.layers import LAYER_ORDER, ContextBreakdown, Layer
from localforge.context.tokens import DEFAULT_CHARS_PER_TOKEN, TokenEstimator

__all__ = [
    "ContextBudget", "ContextBuilder", "BuiltContext",
    "Layer", "ContextBreakdown", "LAYER_ORDER",
    "TokenEstimator", "DEFAULT_CHARS_PER_TOKEN",
]
