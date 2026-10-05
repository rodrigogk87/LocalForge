"""Laboratorio del harness: StateMachine -> verifier -> repair loop.

No es un test de pytest: es una demo para correr a mano y LEER la salida.

    make lab-harness        (o su alias, make lab-verify)

La idea que tiene que quedar a la vista:

    El modelo propone terminar.
    El harness decide si realmente puede terminar.

Vive en `labs/` y no adentro de `worlds/3-harness-w3/` porque el paso 3 es una
foto generada: `make build` la borra y la rehace desde git. El Makefile la corre
con el entorno de ese paso, que es el que tiene `localforge` instalado.

Sin red y sin Ollama. Todo lo que aparece es codigo REAL del paso 3 --
StateMachine, IllegalTransition, TrajectoryVerifier, NoHedgingVerifier,
CompositeVerifier, AgentHarness, ToolExecutor y las tools de filesystem -- con dos
piezas propias, las dos inyectadas por parametros publicos:

  - `ModeloGuionado`: el unico falso. Devuelve respuestas prearmadas en orden,
    igual que el ScriptedProvider de los tests. Hace falta por la misma razon que
    alla: para MOSTRAR un rechazo y una reparacion hay que poder garantizar que el
    modelo se equivoque en el turno 2 y se corrija en el 4, y un LLM real no se
    deja guiar asi.
  - `VerifierQueMira`: envuelve al `default_verifier()` REAL, delega en el y anota
    que respuesta y que trayectoria vio. No decide nada. Existe porque el evento
    `verified` del loop trae el Verdict pero no la trayectoria.

Para narrar los estados sin inventarlos, el lab maneja una StateMachine propia a
partir de los eventos del loop, y al final verifica que su camino sea IDENTICO al
`state_path` del harness. Si la narracion tuviera una transicion ilegal, la propia
clase la rechazaria; si difiriera del camino real, falla la assertion.
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from localforge.config import Settings
from localforge.harness import AgentHarness
from localforge.harness.state import IllegalTransition, StateMachine
from localforge.harness.verify import (
    EVIDENCE_TOOLS,
    HEDGES,
    NoHedgingVerifier,
    TrajectoryVerifier,
    Verdict,
    default_verifier,
)
from localforge.models import (
    AgentMessage,
    AgentStatus,
    AgentTask,
    ModelResponse,
    StopReason,
    ToolCall,
)
from localforge.tools import default_registry

S = AgentStatus
PREVIEW = 64
MAX_REPAIRS = 2


# ---------------------------------------------------------------------------
# Las dos piezas propias del lab
# ---------------------------------------------------------------------------


class ModeloGuionado:
    """Un ModelProvider que devuelve respuestas prearmadas, en orden.

    Anota que mensajes recibio en cada llamada: es la forma de VER que le llega
    al modelo despues de un rechazo, que es justamente el feedback reinyectado.
    """

    name = "guionado"
    model = "guionado"

    def __init__(self, guion: list[ModelResponse]) -> None:
        self.guion = list(guion)
        self.llamadas: list[list[AgentMessage]] = []

    async def complete(self, messages, tools=None, *, system=None, max_tokens=None):  # noqa: ANN001
        self.llamadas.append(list(messages))
        return self.guion.pop(0)

    async def health(self) -> dict[str, str]:
        return {"provider": self.name, "model": self.model}

    async def aclose(self) -> None:
        return None


class VerifierQueMira:
    """Delega en el verifier real y anota lo que vio. No cambia ninguna decision."""

    name = "compuesto"

    def __init__(self, real) -> None:  # noqa: ANN001
        self.real = real
        self.vistos: list[tuple[str, list[str], Verdict]] = []

    def verify(self, task, answer, trajectory):  # noqa: ANN001
        verdict = self.real.verify(task, answer, trajectory)
        # list(): la trayectoria es la MISMA lista que el loop sigue llenando. Sin
        # la copia, al final veriamos la trayectoria completa en todos los registros.
        self.vistos.append((answer, list(trajectory), verdict))
        return verdict


# ---------------------------------------------------------------------------
# Datos del escenario
# ---------------------------------------------------------------------------

TASK = "Cual es el archivo mas importante de este proyecto y que hace?"

# Un cli.py GRANDE y un app.py chico que es el que de verdad hace el trabajo. Es
# la trampa en la que cayo el agente en su primera corrida real: concluyo que
# cli.py era el archivo mas importante POR SU TAMAÑO EN KB, sin abrir ninguno.
APP_PY = (
    "def main():\n"
    '    """Punto de entrada real: carga la config y arranca el servidor."""\n'
    "    config = cargar_config()\n"
    "    return arrancar(config)\n"
)
CLI_PY = "# parser de argumentos, mucho boilerplate\n" + "\n".join(
    f"OPCION_{i} = '--opcion-{i}'" for i in range(180)
)

GUION = [
    # turno 1: se orienta
    ModelResponse(
        tool_calls=[ToolCall(id="c1", name="list_files", arguments={})],
        stop_reason=StopReason.TOOL_USE, input_tokens=420, output_tokens=12,
    ),
    # turno 2: responde SIN haber abierto nada. Propone terminar.
    ModelResponse(
        content="Posiblemente cli.py sea el archivo mas importante: es el mas grande del repo.",
        stop_reason=StopReason.END_TURN, input_tokens=610, output_tokens=24,
    ),
    # turno 3: tras el feedback, abre el archivo
    ModelResponse(
        tool_calls=[ToolCall(id="c3", name="read_file", arguments={"path": "app.py"})],
        stop_reason=StopReason.TOOL_USE, input_tokens=790, output_tokens=15,
    ),
    # turno 4: responde citando lo que leyo. Vuelve a proponer terminar.
    ModelResponse(
        content="El archivo importante es app.py: main() en la linea 1 carga la config "
        "(linea 3) y arranca el servidor (linea 4). cli.py es solo el parser de argumentos.",
        stop_reason=StopReason.END_TURN, input_tokens=980, output_tokens=40,
    ),
]


# ---------------------------------------------------------------------------
# Helpers de impresion
# ---------------------------------------------------------------------------


def title(text: str) -> None:
    print(f"\n{'=' * 78}\n=== {text}\n{'=' * 78}")


def sub(text: str) -> None:
    print(f"\n--- {text} " + "-" * max(0, 72 - len(text)))


def preview(text: str | None, n: int = PREVIEW) -> str:
    if not text:
        return "(vacio)"
    text = " ".join(text.split())
    return text if len(text) <= n else f"{text[:n - 3]}..."


def verdict_str(v: Verdict) -> str:
    return "PASS" if v.ok else f"REJECT ({v.check})"


def wrap(text: str, indent: str = "   ", width: int = 74) -> str:
    out, line = [], ""
    for word in text.split():
        if len(line) + len(word) + 1 > width - len(indent):
            out.append(indent + line)
            line = word
        else:
            line = f"{line} {word}".strip()
    if line:
        out.append(indent + line)
    return "\n".join(out)


# ---------------------------------------------------------------------------
# 1. Maquina de estados
# ---------------------------------------------------------------------------

ORDEN = [S.CREATED, S.RUNNING, S.WAITING_TOOL, S.VERIFYING, S.REPAIRING,
         S.COMPLETED, S.FAILED, S.CANCELLED]
CORTO = {S.CREATED: "CRE", S.RUNNING: "RUN", S.WAITING_TOOL: "WAI", S.VERIFYING: "VER",
         S.REPAIRING: "REP", S.COMPLETED: "COM", S.FAILED: "FAI", S.CANCELLED: "CAN"}


def matriz() -> tuple[dict, set]:
    """El grafo de transiciones, derivado SOLO de la API publica: StateMachine.can().

    Se pone una maquina en cada estado y se le pregunta a donde puede ir. Asi la
    tabla sale del codigo real y no de una copia del dict privado _ALLOWED.
    """
    puede = {s: {t for t in ORDEN if StateMachine(status=s).can(t)} for s in ORDEN}
    no_terminales = [s for s in ORDEN if not s.is_terminal]
    # Salidas de emergencia: destinos alcanzables desde TODO estado no terminal.
    aborts = {t for t in ORDEN if all(t in puede[s] for s in no_terminales)}
    return puede, aborts


def seccion_1() -> dict:
    title("1. MAQUINA DE ESTADOS")
    print("Dos cosas distintas que conviene no mezclar:\n")
    print("   AgentStatus  = los estados que EXISTEN       (un enum, vive en models.py)")
    print("   StateMachine = las transiciones PERMITIDAS   (vive en harness/state.py)")

    sub("1a. AgentStatus: los estados que existen")
    for s in ORDEN:
        print(f"   {s.name:13} terminal={str(s.is_terminal):5}")
    print("\n   Un enum dice que valores hay. No puede decir que de VERIFYING no se vuelve")
    print("   a WAITING_TOOL: eso no es un valor, es una regla entre valores.")

    sub("1b. StateMachine: las transiciones permitidas (de StateMachine.can())")
    puede, aborts = matriz()
    print(f"   {'desde/hacia':13}" + "".join(f"{CORTO[t]:>5}" for t in ORDEN))
    for s in ORDEN:
        celdas = []
        for t in ORDEN:
            if t in puede[s]:
                celdas.append("    !" if t in aborts else "    X")
            else:
                celdas.append("    .")
        print(f"   {s.name:13}" + "".join(celdas))
    print("\n   X = paso del flujo normal   ! = salida de emergencia   . = prohibido")
    print("   Mira la columna COM: hay UNA sola X, en la fila VERIFYING.")
    print("   A COMPLETED solo se llega pasando por la verificacion.")

    sub("1c. Un recorrido valido")
    m = StateMachine()
    print(f"   {m.status.name}")
    for paso in (S.RUNNING, S.WAITING_TOOL, S.RUNNING, S.VERIFYING, S.COMPLETED):
        anterior = m.status
        m.to(paso)
        print(f"   -> {paso.name:13} (StateMachine acepto {anterior.name} -> {paso.name})")
    print(f"\n   m.path()         = {m.path()}")
    print(f"   m.compact_path() = {m.compact_path()}")

    sub("1d. Una transicion prohibida: RUNNING -> COMPLETED")
    m2 = StateMachine()
    m2.to(S.RUNNING)
    print("   m2 = StateMachine(); m2.to(RUNNING)")
    print(f"   m2.status antes        = {m2.status.name}")
    print("   m2.to(COMPLETED) ...")
    error = ""
    try:
        m2.to(S.COMPLETED)
    except IllegalTransition as exc:
        error = str(exc)
        print("   IllegalTransition:")
        print(wrap(error, indent="      "))
    print(f"   m2.status despues      = {m2.status.name}   (el intento fallido no movio nada)")
    print("\n   Por que esta prohibida: terminar sin pasar por VERIFYING seria dejar que el")
    print("   modelo sea juez de su propio trabajo. Y es una EXCEPCION, no un ToolResult,")
    print("   porque no es un error del modelo: si el loop intentara esto, es un bug nuestro.")

    return {"valido": m, "fallido": m2, "error": error, "puede": puede, "aborts": aborts}


# ---------------------------------------------------------------------------
# 2 y 3. El harness de verdad: rechazo y reparacion
# ---------------------------------------------------------------------------


class Narrador:
    """Escucha los eventos del loop y los traduce a transiciones.

    Maneja una StateMachine REAL con las mismas reglas que loop.py: si alguna
    transicion narrada fuera ilegal, `to()` levantaria IllegalTransition. Al
    final se compara su camino con el state_path del harness.
    """

    def __init__(self) -> None:
        self.m = StateMachine()
        self.por_turno: dict[int, list[str]] = {}

    def _ir(self, turno: int, destino: AgentStatus) -> None:
        anterior = self.m.status
        self.m.to(destino, turn=turno)
        self.por_turno.setdefault(turno, []).append(f"{anterior.name} -> {destino.name}")

    def __call__(self, event: str, **p) -> None:  # noqa: ANN003
        t = p.get("turn", 0)
        if event == "turn_start" and self.m.status is S.CREATED:
            self._ir(t, S.RUNNING)
        elif event == "tools_start":
            self._ir(t, S.WAITING_TOOL)
        elif event == "tools_done":
            self._ir(t, S.RUNNING)
        elif event == "model_response":
            r = p["response"]
            if not r.tool_calls and r.stop_reason is not StopReason.MAX_TOKENS:
                self._ir(t, S.VERIFYING)
        elif event == "verified":
            if p["verdict"].ok:
                self._ir(t, S.COMPLETED)
            elif self.m.repairs >= MAX_REPAIRS:
                self._ir(t, S.FAILED)
            else:
                self._ir(t, S.REPAIRING)
                self._ir(t, S.RUNNING)


def turno(n: int, narr: Narrador) -> None:
    r = GUION_ORIGINAL[n - 1]
    if r.tool_calls:
        c = r.tool_calls[0]
        args = ", ".join(f"{k}={v!r}" for k, v in c.arguments.items())
        print(f"   turno {n}  el modelo pide una tool: {c.name}({args})")
    else:
        print(f"   turno {n}  el modelo PROPONE TERMINAR (end_turn, sin tools):")
        print(f"            \"{preview(r.content, 60)}\"")
    for paso in narr.por_turno.get(n, []):
        print(f"            estado: {paso}")


def seccion_2_y_3() -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        (repo / "app.py").write_text(APP_PY, encoding="utf-8")
        (repo / "cli.py").write_text(CLI_PY, encoding="utf-8")

        provider = ModeloGuionado(GUION)
        mirador = VerifierQueMira(default_verifier())
        narr = Narrador()
        harness = AgentHarness(
            provider, default_registry(), cfg=Settings(),
            on_event=narr, verifier=mirador, max_repairs=MAX_REPAIRS,
        )
        task = AgentTask(objective=TASK, repo_path=str(repo), max_turns=10)
        outcome = asyncio.run(harness.run(task))

    # -- 2. el rechazo -------------------------------------------------------
    title("2. RESPUESTA SIN EVIDENCIA")
    print(f"   tarea: \"{TASK}\"")
    print("   repo temporal: app.py (4 lineas, el que hace el trabajo)")
    print(f"                  cli.py ({len(CLI_PY.splitlines())} lineas de boilerplate, el mas grande)")

    sub("2a. por que list_files no cuenta como evidencia")
    print(f"   EVIDENCE_TOOLS = {sorted(EVIDENCE_TOOLS)}")
    print("   list_files esta en el registry, pero NO en esa lista:\n")
    tv = TrajectoryVerifier()
    t_demo = AgentTask(objective=TASK, repo_path=".")
    for tray in (["list_files"], ["list_files", "read_file"], ["search_code"]):
        v = tv.verify(t_demo, "respuesta", tray)
        print(f"   trayectoria {str(tray):30} -> {verdict_str(v)}")
    print("\n   Un listado dice como se LLAMAN las cosas, no que HACEN. Aceptarlo como")
    print("   evidencia seria certificar exactamente la adivinanza que el verifier vino a")
    print("   impedir: en su primera corrida real, el agente listo el arbol y dijo que")
    print("   cli.py era el mas importante POR SU TAMAÑO EN KB.")

    sub("2b. el harness corriendo, hasta el rechazo")
    for n in (1, 2):
        turno(n, narr)
    print()
    answer, tray, v1 = mirador.vistos[0]
    print(f"   verifier: {verdict_str(v1)}")

    sub("2c. que vio el verifier, y que decidio")
    print(f"   respuesta  : \"{preview(answer, 60)}\"")
    print(f"   trayectoria: {tray}")
    print("\n   default_verifier() = CompositeVerifier, que corre estos en orden:")
    for sv in mirador.real.verifiers:
        r = sv.verify(task, answer, tray)
        print(f"      {sv.name:18} -> {verdict_str(r)}")
    print("\n   Los DOS rechazan, pero el compuesto devuelve solo el PRIMERO. A proposito:")
    print("   con dos reproches juntos el modelo atiende uno y deja el otro.")
    print("\n   el Verdict que vuelve al loop:")
    print(f"      ok       = {v1.ok}")
    print(f"      check    = {v1.check!r}")
    print("      feedback =")
    print(wrap(v1.feedback, indent="         "))

    # -- 3. la reparacion ----------------------------------------------------
    title("3. REPAIR LOOP")

    sub("3a. el feedback que se reinyecta al contexto del modelo")
    llamada = provider.llamadas[2]  # la llamada del turno 3, despues del rechazo
    print(f"   en el turno 3 el modelo recibe {len(llamada)} mensajes. Los dos ultimos son nuevos:\n")
    for m in llamada[-2:]:
        print(f"   [{m.role}]")
        print(wrap(m.content or "", indent="      "))
        print()
    print("   El loop NO le dice \"verificacion fallida\": le pasa el texto del Verdict tal")
    print("   cual. Por eso el feedback esta escrito PARA el modelo -- dice que hacer.")

    sub("3b. el modelo vuelve a ejecutarse")
    for n in (3, 4):
        turno(n, narr)
    _, tray2, v2 = mirador.vistos[1]
    print()
    print(f"   trayectoria que ve ahora el verifier: {tray2}")
    print(f"   verifier: {verdict_str(v2)}")

    sub("3c. el resultado")
    print(f"   status     = {outcome.status.value}")
    print(f"   reason     = {outcome.reason}")
    print(f"   repairs    = {outcome.repairs}")
    print(f"   rejected_by= {outcome.rejected_by}")
    print(f"   trajectory = {outcome.trajectory}")
    print("   state_path =")
    print(wrap(outcome.state_path.upper(), indent="      "))
    print("\n   respuesta final:")
    print(wrap(outcome.output, indent="      "))

    return {"outcome": outcome, "provider": provider, "mirador": mirador, "narr": narr,
            "v1": v1, "v2": v2, "tray1": tray, "tray2": tray2}


# Copia del guion para narrar: el provider va consumiendo el suyo.
GUION_ORIGINAL = list(GUION)


# ---------------------------------------------------------------------------
# 4. NoHedgingVerifier
# ---------------------------------------------------------------------------


def seccion_4() -> list:
    title("4. NoHedgingVerifier: no es una lista de palabras prohibidas")
    nh = NoHedgingVerifier()
    print(f"   HEDGES (parcial) = {list(HEDGES[:5])} ...")
    print(f"   min_reads        = {nh.min_reads}   (lecturas a partir de las cuales tolera)\n")

    task = AgentTask(objective="explicame el repo", repo_path=".")
    casos = [
        ("Posiblemente cli.py sea el entrypoint.", ["read_file"]),
        ("Probablemente el resto del repo siga el mismo patron.",
         ["read_file", "read_file", "search_code"]),
        ("Posiblemente cli.py sea el entrypoint.",
         ["read_file", "read_file", "search_code"]),
        ("app.py define main() en la linea 1.", ["read_file"]),
    ]
    print(f"   {'respuesta':44} {'lecturas':>8}   resultado")
    print("   " + "-" * 72)
    resultados = []
    for texto, tray in casos:
        reads = sum(1 for t in tray if t in EVIDENCE_TOOLS)
        v = nh.verify(task, texto, tray)
        resultados.append((texto, reads, v))
        print(f"   {preview(texto, 44):44} {reads:>8}   {verdict_str(v)}")
    print()
    print("   Mira la fila 1 contra la fila 3: el MISMO texto, \"posiblemente\" incluido,")
    print("   rechazado con 1 lectura y aceptado con 3.")
    print()
    print("   Si fuera una busqueda de palabras, las dos se rechazarian. Pero especular")
    print("   sobre lo que NO leiste, despues de leer bastante, es honestidad epistemica,")
    print("   no adivinanza. Un verifier que castigara eso le enseñaria al modelo a")
    print("   afirmar con seguridad cosas que no verifico -- que es peor.")
    return resultados


# ---------------------------------------------------------------------------
# Cierre
# ---------------------------------------------------------------------------


def main() -> None:
    s1 = seccion_1()
    s23 = seccion_2_y_3()
    s4 = seccion_4()

    out = s23["outcome"]
    narr = s23["narr"]
    provider = s23["provider"]
    mirador = s23["mirador"]

    title("EL MODELO PROPONE TERMINAR. EL HARNESS DECIDE SI PUEDE.")
    propuestas = [(i + 1, r) for i, r in enumerate(GUION_ORIGINAL) if not r.tool_calls]
    for (n, r), (_, tray, v) in zip(propuestas, mirador.vistos):
        decision = "SI, terminar" if v.ok else f"NO -- {v.check}"
        print(f"   turno {n}: el modelo propone terminar  ->  el harness dice: {decision}")
        print(f"            (con la trayectoria {tray})")
    print()
    print("   Lo que el modelo controla: cuando DEJA DE PEDIR tools.")
    print("   Lo que decide el harness:  si eso alcanza para TERMINAR.")

    # -- assertions ------------------------------------------------------------
    title("CHEQUEOS")
    checks = []

    m = s1["valido"]
    assert m.status is S.COMPLETED
    assert m.path() == ["created", "running", "waiting_tool", "running", "verifying", "completed"]
    checks.append("el recorrido valido termino en COMPLETED pasando por VERIFYING")

    assert s1["error"], "RUNNING -> COMPLETED no levanto IllegalTransition"
    assert s1["fallido"].status is S.RUNNING
    assert len(s1["fallido"].history) == 1
    checks.append("RUNNING -> COMPLETED levanto IllegalTransition y no movio el estado")

    puede, aborts = s1["puede"], s1["aborts"]
    assert {s for s in ORDEN if S.COMPLETED in puede[s]} == {S.VERIFYING}
    assert aborts == {S.FAILED, S.CANCELLED}
    checks.append("a COMPLETED solo se llega desde VERIFYING; FAILED/CANCELLED desde cualquiera")

    assert "list_files" not in EVIDENCE_TOOLS
    assert s23["tray1"] == ["list_files"] and not s23["v1"].ok
    assert s23["v1"].check == "trayectoria"
    checks.append("con trayectoria ['list_files'] el verifier rechazo (trayectoria)")

    feedback = s23["v1"].feedback
    reinyectado = provider.llamadas[2][-1]
    assert reinyectado.role == "user" and reinyectado.content == feedback
    checks.append("el feedback del Verdict llego TAL CUAL como mensaje 'user' al modelo")

    assert s23["v2"].ok and "read_file" in s23["tray2"]
    assert out.status is S.COMPLETED and out.reason is None
    assert out.repairs == 1 and out.rejected_by == ["trayectoria"]
    checks.append("tras leer app.py el verifier acepto: COMPLETED con 1 reparacion")

    assert narr.m.compact_path() == out.state_path, (narr.m.compact_path(), out.state_path)
    assert "verifying -> repairing" in out.state_path and out.state_path.endswith("completed")
    checks.append("el camino narrado coincide EXACTO con el state_path interno del harness")

    (_, r1, v_a), (_, r2, v_b), (_, r3, v_c), (_, r4, v_d) = s4
    assert not v_a.ok and v_b.ok and v_c.ok and v_d.ok
    assert r1 < s23["mirador"].real.verifiers[1].min_reads <= r3
    checks.append("NoHedging: el mismo 'posiblemente' se rechaza con 1 lectura y pasa con 3")

    for c in checks:
        print(f"  OK  {c}")
    print("\ntodas las assertions pasaron.")


if __name__ == "__main__":
    main()
