"""Retrieval just-in-time (W2-C11): traer el fragmento, no el archivo.

La Fase 2 lo dejo bloqueado por escrito: *"traer el fragmento exacto requiere,
primero, poder encontrarlo"*. `search_code` resolvio el encontrar; esto resuelve
el traer.

La diferencia con las dos tools que ya habia:

    search_code  "safe_path"          -> lineas sueltas donde aparece la palabra
    read_file    "tools/fs.py"        -> el archivo entero (o una ventana)
    retrieve     "donde se valida..." -> los 3-5 BLOQUES de codigo mas relevantes,
                                         enteros y numerados, de cualquier archivo

`search_code` exige saber la palabra exacta. `retrieve` acepta la pregunta en
lenguaje natural y ordena por relevancia. Es lexico (BM25), no semantico: no hay
modelo de embeddings, a proposito. Un modelo de embeddings es otra descarga, otra
dependencia y otro numero que calibrar, y para codigo -- donde la pregunta casi
siempre contiene un identificador -- BM25 con los identificadores partidos
(`safe_path` -> `safe`, `path`) rinde sorprendentemente bien.

El indice vive aca; la tool que lo expone es `tools/retrieve.py`. Separados porque
la memoria entre sesiones (harness/memory.py) usa el mismo ranking sin ser una tool.

Dos decisiones que no son obvias:

1. **Se trocea por unidad de codigo, no por cantidad de lineas.** En Python, cada
   `def`/`class` de primer nivel es un fragmento. Un trozo que corta una funcion
   a la mitad le da al modelo media respuesta y la certeza de tenerla entera.

2. **Los archivos sensibles NO se indexan.** `read_file(".env")` lo frena la
   politica de permisos porque mira el argumento `path`. `retrieve` no tiene
   `path`: devuelve fragmentos de cualquier archivo. Si el indice incluyera el
   `.env`, una pregunta como "que password usa la base" lo traeria y el permiso
   nunca se habria enterado. El filtro tiene que estar en el indice.
"""

from __future__ import annotations

import fnmatch
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from localforge.permissions import SENSITIVE_GLOBS

# Igual que search_code: un archivo enorme suele ser generado y no aporta.
MAX_FILE_BYTES = 500_000

# Ventana para lo que no es Python (o un Python sin defs). Con solapamiento para
# que una idea que cae en el borde no quede partida en dos fragmentos inutiles.
WINDOW = 40
OVERLAP = 10

# Un fragmento de 400 lineas (una clase gigante) es un read_file disfrazado.
MAX_CHUNK_LINES = 80

# Parametros clasicos de BM25. No se calibran: el orden relativo es lo que
# importa y con estos valores es estable.
K1 = 1.2
B = 0.75

# Palabras que aparecen en cualquier pregunta y no discriminan nada.
STOPWORDS = frozenset(
    """
    a al como con cual cuales de del donde el en es esta este esto hace hay la las
    lo los mas para por que se si su sus un una y o the of to in is and for how
    what where which does do def self return none true false import from
    """.split()
)

_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_CAMEL = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|[0-9]+")
_TOP_LEVEL = re.compile(r"^(?:async\s+def|def|class)\s+\w+", re.M)


def tokenize(text: str) -> list[str]:
    """Identificadores partidos: `safe_path` -> safe_path, safe, path.

    Se conserva el identificador entero Y sus partes. Entero, porque una
    pregunta que nombra `safe_path` tiene que pegarle fuerte a donde se define;
    partido, porque "donde se valida la ruta del path" tiene que encontrarlo
    aunque nunca escriba el guion bajo.
    """
    out: list[str] = []
    for ident in _IDENT.findall(text):
        low = ident.lower()
        parts = [p.lower() for chunk in ident.split("_") for p in _CAMEL.findall(chunk)]
        for tok in (low, *parts) if len(parts) > 1 else (low,):
            if len(tok) >= 2 and tok not in STOPWORDS:
                out.append(tok)
    return out


def is_sensitive(rel: str) -> bool:
    name = rel.rsplit("/", 1)[-1]
    return any(fnmatch.fnmatch(name, g) or fnmatch.fnmatch(rel, g) for g in SENSITIVE_GLOBS)


@dataclass(frozen=True)
class Chunk:
    path: str
    start: int  # 1-indexed, inclusivo
    end: int
    text: str

    @property
    def ref(self) -> str:
        return f"{self.path}:{self.start}-{self.end}"


def chunk_file(rel: str, text: str) -> list[Chunk]:
    lines = text.splitlines()
    if not lines:
        return []
    starts: list[int] = []
    if rel.endswith(".py"):
        starts = [text.count("\n", 0, m.start()) for m in _TOP_LEVEL.finditer(text)]
    if starts:
        # El encabezado (imports, constantes, docstring) es un fragmento propio.
        bounds = ([0] if starts[0] > 0 else []) + starts + [len(lines)]
        spans = list(zip(bounds, bounds[1:]))
    else:
        spans = [
            (i, min(i + WINDOW, len(lines)))
            for i in range(0, max(1, len(lines) - OVERLAP), WINDOW - OVERLAP)
        ]
    out: list[Chunk] = []
    for a, b in spans:
        # Un def gigante se parte en ventanas: mejor dos fragmentos que uno que
        # se coma el contexto entero.
        for s in range(a, b, MAX_CHUNK_LINES):
            e = min(b, s + MAX_CHUNK_LINES)
            # Las lineas en blanco del final no son del fragmento: la referencia
            # `fs.py:6-12` tiene que terminar donde termina el codigo.
            while e > s + 1 and not lines[e - 1].strip():
                e -= 1
            body = "\n".join(lines[s:e])
            if body.strip():
                out.append(Chunk(rel, s + 1, e, body))
    return out


@dataclass
class Index:
    chunks: list[Chunk] = field(default_factory=list)
    tfs: list[Counter[str]] = field(default_factory=list)
    skipped_sensitive: list[str] = field(default_factory=list)

    @classmethod
    def build(cls, workspace: Path) -> Index:
        # Import diferido: este modulo lo usan tools/ Y harness/memory.py, y
        # tools/ importa este modulo. Arriba, el import seria circular.
        from localforge.tools.fs import _is_ignored

        root = workspace.resolve()
        idx = cls()
        for path in sorted(root.rglob("*")):
            if not path.is_file() or _is_ignored(path, root):
                continue
            rel = path.relative_to(root).as_posix()
            if is_sensitive(rel):
                idx.skipped_sensitive.append(rel)
                continue
            try:
                if path.stat().st_size > MAX_FILE_BYTES:
                    continue
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for chunk in chunk_file(rel, text):
                # El nombre del archivo es parte del contenido: "el cli" tiene
                # que encontrar cli.py aunque adentro no diga "cli".
                tf = Counter(tokenize(chunk.text) + tokenize(rel))
                idx.chunks.append(chunk)
                idx.tfs.append(tf)
        return idx

    def search(self, query: str, k: int = 4) -> list[tuple[float, Chunk]]:
        scores = bm25(self.tfs, tokenize(query))
        scored = [(sc, ch) for sc, ch in zip(scores, self.chunks) if sc > 0]
        scored.sort(key=lambda sc: (-sc[0], sc[1].path, sc[1].start))
        return scored[:k]


def bm25(docs: list[Counter[str]], query_terms: list[str]) -> list[float]:
    """Puntaje BM25 de cada documento para la consulta.

    Aparte de `Index` porque la memoria entre sesiones (harness/memory.py) rankea
    recuerdos con la misma regla: dos rankings distintos para "lo mas parecido a
    esta pregunta" serian dos bugs posibles en vez de uno.
    """
    terms = list(dict.fromkeys(query_terms))
    n = len(docs)
    if not terms or not n:
        return [0.0] * n
    df: Counter[str] = Counter()
    for tf in docs:
        df.update(t for t in terms if t in tf)
    avg = sum(sum(tf.values()) for tf in docs) / n or 1.0
    out: list[float] = []
    for tf in docs:
        length = sum(tf.values()) or 1
        score = 0.0
        for t in terms:
            f = tf.get(t)
            if not f:
                continue
            idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
            score += idf * f * (K1 + 1) / (f + K1 * (1 - B + B * length / avg))
        out.append(score)
    return out


__all__ = ["Chunk", "Index", "bm25", "chunk_file", "tokenize", "is_sensitive"]
