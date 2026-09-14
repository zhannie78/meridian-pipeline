#!/usr/bin/env python3
"""Meridian pipeline intelligence: ask a question, get an answer you can recompute.

    python3 ask.py "How is the Enterprise segment tracking against quota this quarter?"
    python3 ask.py "Why is Priya at risk?" --full    # print every source row
    python3 ask.py audit                             # the data-integrity report
    python3 ask.py demo                              # the brief's questions and follow-ups
    python3 ask.py                                   # interactive

Every answer prints ANSWER, SUPPORTING METRICS, SOURCE DATA, METHOD, ASSUMPTIONS
and FLAGS. When a language model is enabled, it only fills in the raw query
that METHOD step 1 shows after validation; every figure is computed by
meridian/metrics.py from the rows listed under SOURCE DATA.
"""
from __future__ import annotations

import sys

from meridian import answer
from meridian.data import DataIntegrityError
from meridian.engine import Engine
from meridian.query import CAPABILITIES, QueryRejected

DEMO_QUESTIONS = [
    "How is the Enterprise segment tracking against quota this quarter?",
    "Which reps are at risk of missing Q2?",
    "How does Q2 attainment compare to where we were at the same point in Q1?",
    "What's our pipeline coverage this quarter?",
    "Show me the Enterprise deals.",
    "Why is Priya at risk?",
    "Which deals make up the open pipeline?",
]


def respond(engine: Engine, question: str, full: bool = False) -> str:
    try:
        return answer.render(engine.ask(question), full=full)
    except QueryRejected as exc:
        return answer.render_rejection(exc, CAPABILITIES)
    except DataIntegrityError as exc:
        return answer.render_data_error(exc)


def main(argv) -> int:
    full = "--full" in argv
    args = [a for a in argv[1:] if a != "--full"]
    try:
        engine = Engine()
    except DataIntegrityError as exc:
        print(answer.render_data_error(exc))
        return 1

    if args == ["audit"]:
        try:
            print(answer.render_audit(engine.snaps))
        except DataIntegrityError as exc:
            print(answer.render_data_error(exc))
            return 1
        return 0

    if args == ["demo"]:
        print(answer.render_audit(engine.snaps))
        for q in DEMO_QUESTIONS:
            print("\n\n" + answer.BOLD("> " + q))
            print(respond(engine, q, full))
        return 0

    if args:
        print(respond(engine, " ".join(args), full))
        return 0

    print(answer.BOLD("Meridian pipeline intelligence  |  data as of %s  |  parser: %s"
                      % (engine.manifest.as_of("live").date()
                         if engine.manifest.as_of("live") is not None else "unknown",
                         engine.parser.name)))
    print(answer.DIM("Ask a question, type 'audit', or press Ctrl-D to exit. "
                     "Each question is answered on its own.\n"))
    while True:
        try:
            q = input(answer.CYN("> ")).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not q:
            continue
        if q.lower() in ("quit", "exit"):
            return 0
        if q.lower() == "audit":
            print(answer.render_audit(engine.snaps))
            continue
        print(respond(engine, q, full))


if __name__ == "__main__":
    sys.exit(main(sys.argv))
