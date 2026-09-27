"""Ruteo: argparse y nada mas.

    localforge health
    localforge ask <repo> "<objetivo>" [--save] [--read-only]
    localforge resume <id>
    localforge runs
    localforge eval [repo]
"""

from __future__ import annotations

import argparse
import asyncio

from localforge.cli.commands import cmd_ask, cmd_eval, cmd_health, cmd_resume, cmd_runs
from localforge.config import settings

def main() -> int:
    parser = argparse.ArgumentParser(prog="localforge", description="Coding agent local")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("health", help="Verifica que el LLM local responde")

    ask = sub.add_parser("ask", help="Corre el agente sobre un repositorio")
    ask.add_argument("repo", help="Ruta del repositorio")
    ask.add_argument("objective", help="Que queres que haga el agente")
    ask.add_argument("-v", "--verbose", action="store_true", help="Muestra el texto del modelo")
    ask.add_argument("--max-turns", type=int, default=settings.max_turns)
    ask.add_argument(
        "--read-only",
        action="store_true",
        help="Deniega toda tool con efectos sin preguntar. Para repos que no son tuyos.",
    )
    ask.add_argument(
        "--save",
        action="store_true",
        help="Guarda un checkpoint por turno para poder retomar con `resume`.",
    )

    res = sub.add_parser("resume", help="Retoma una corrida guardada")
    res.add_argument("task_id", help="Id de la corrida (lo lista `localforge runs`)")
    res.add_argument("-v", "--verbose", action="store_true")

    sub.add_parser("runs", help="Lista las corridas guardadas")

    ev = sub.add_parser("eval", help="Corre el dataset de golden tasks y reporta")
    ev.add_argument("repo", nargs="?", default=".", help="Repositorio sobre el que evaluar")
    ev.add_argument("-v", "--verbose", action="store_true")

    args = parser.parse_args()

    if args.command == "health":
        return asyncio.run(cmd_health())
    if args.command == "ask":
        return asyncio.run(
            cmd_ask(
                args.repo,
                args.objective,
                verbose=args.verbose,
                max_turns=args.max_turns,
                read_only=args.read_only,
                save=args.save,
            )
        )
    if args.command == "resume":
        return asyncio.run(cmd_resume(args.task_id, verbose=args.verbose))
    if args.command == "runs":
        return cmd_runs()
    if args.command == "eval":
        return asyncio.run(cmd_eval(args.repo, verbose=args.verbose))
    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["main"]
