"""Las nueve capas que compiten por la ventana, y cuanto ocupa cada una.

Segunda pieza del Mundo 2. Los nombres de las capas son los de W2-C8. Las que
LocalForge todavia no tiene se reportan igual con `present=False`: un 0 explicito
al lado de `retrieved` dice que la capa existe en el diseño y falta, y asi la
tabla tambien es el backlog.
"""

from __future__ import annotations

from dataclasses import dataclass

# Los nombres son los de W2-C8. Las que todavia no existen en LocalForge se
# reportan igual, con present=False: ver un 0 explicito al lado de "retrieved"
# dice mas sobre el estado del proyecto que no listar la capa.
LAYER_ORDER = (
    "instructions",
    "tools",
    "task",
    "conversation",
    "observations",
    "environment",
    "skills",
    "memory",
    "retrieved",
)


@dataclass(frozen=True)
class Layer:
    name: str
    tokens: int
    chars: int
    # Si se puede tirar o resumir cuando falta lugar. instructions y task no:
    # sin el system prompt el agente no sabe trabajar, y sin la task no sabe
    # que tiene que hacer.
    compactable: bool = False
    present: bool = True


@dataclass(frozen=True)
class ContextBreakdown:
    """Foto del contexto de UN turno, por capa."""

    layers: tuple[Layer, ...]
    limit: int
    reserved_output: int
    compacted_messages: int = 0
    recovered_tokens: int = 0

    @property
    def total(self) -> int:
        return sum(l.tokens for l in self.layers)

    @property
    def available(self) -> int:
        """Lo que queda para input despues de reservar la salida."""
        return max(0, self.limit - self.reserved_output)

    @property
    def pct(self) -> float:
        return (self.total / self.available * 100) if self.available else 0.0

    @property
    def fits(self) -> bool:
        return self.total <= self.available

    def layer(self, name: str) -> Layer | None:
        return next((l for l in self.layers if l.name == name), None)

    def table(self) -> str:
        """Render para la CLI. La capa mas grande primero: es la que hay que atacar."""
        rows = []
        present = [l for l in self.layers if l.present]
        for l in sorted(present, key=lambda x: -x.tokens):
            share = (l.tokens / self.total * 100) if self.total else 0
            mark = "~" if l.compactable else " "
            rows.append(f"  {mark}{l.name:14} {l.tokens:>7} tok  {share:>5.1f}%")
        missing = [l.name for l in self.layers if not l.present]
        if missing:
            rows.append(f"   (sin usar: {', '.join(missing)})")
        head = f"  contexto: {self.total} / {self.available} tok ({self.pct:.1f}%)"
        if self.compacted_messages:
            head += f" · compactado: {self.compacted_messages} obs, -{self.recovered_tokens} tok"
        return head + "\n" + "\n".join(rows)


__all__ = ["Layer", "ContextBreakdown", "LAYER_ORDER"]
