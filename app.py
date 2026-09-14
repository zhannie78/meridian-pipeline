#!/usr/bin/env python3
"""Meridian pipeline intelligence in the browser.

    python3 app.py                         # serve on http://127.0.0.1:8765 and open it
    python3 app.py --port 9000 --no-browser
    python3 app.py --host 0.0.0.0 --port 8080 --no-browser   # in a container
    python3 app.py --export recorded.html  # a self-contained recorded run, no server needed

The page is a view over the same engine as ask.py. It sends the question to
/api/ask and renders the JSON it gets back; it never computes a figure.

Standard library only. Binds to 127.0.0.1 unless --host says otherwise. If
MERIDIAN_PASSWORD is set, every request needs it (HTTP basic auth, any username).
"""
from __future__ import annotations

import base64
import hmac
import json
import os
import re
import sys
import threading
import webbrowser
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from meridian import serialize
from meridian.data import (QUARTERS, YEAR, DataIntegrityError, day_of_quarter,
                           quarter_length, quarter_of)
from meridian.engine import Engine
from meridian.query import CAPABILITIES, QueryRejected

HERE = os.path.dirname(os.path.abspath(__file__))
PAGE = os.path.join(HERE, "web", "index.html")
MAX_QUESTION = 500

SUGGESTIONS = [
    {"group": "Core queries", "questions": [
        "How is the Enterprise segment tracking against quota this quarter?",
        "Which reps are at risk of missing Q2?",
        "How does Q2 attainment compare to where we were at the same point in Q1?",
    ]},
    {"group": "Follow-up examples", "questions": [
        "What's our pipeline coverage this quarter?",
        "Is Mid-Market on track this quarter?",
        "Show me the Enterprise deals.",
        "Why is Priya at risk?",
        "Which deals make up the open pipeline?",
    ]},
    {"group": "Try to break it", "questions": [
        "Why is John Smith at risk?",
        "How is Lisa tracking against quota?",
        "What did we book in April?",
        "Why is Marcus at risk?",
        "What will we close next quarter?",
    ]},
]


CORE_QUERIES = SUGGESTIONS[0]["questions"]
# Questions that show the naive contrast: the core queries, plus a follow-up where
# the contrast is especially telling (the naive count includes deals from every quarter).
NAIVE_CONTRAST = CORE_QUERIES + ["Show me the Enterprise deals."]


def _normal(question: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9%]+", " ", question.lower()).split())


def is_core_query(question: str) -> bool:
    """Whether this question shows the naive contrast (see NAIVE_CONTRAST)."""
    return _normal(question) in {_normal(q) for q in NAIVE_CONTRAST}


def _span(quarter: str) -> str:
    start, end = QUARTERS[quarter]
    fmt = lambda d: "%s %d" % (d.strftime("%b"), d.day)
    return "%s\u2013%s" % (fmt(start), fmt(end))


def quarter_status(as_of) -> list:
    """Header lines: each quarter's dates and where it stands on the live snapshot date."""
    current = quarter_of(as_of)
    out = []
    for q in QUARTERS:
        detail = _span(q)
        if as_of is None:
            detail += ", snapshot date unknown"
        elif as_of > QUARTERS[q][1]:
            detail += ", closed"
        elif q == current:
            day, length = day_of_quarter(q, as_of), quarter_length(q)
            detail += ", day %d of %d (%d%%), as of %s" % (day, length, round(100.0 * day / length), as_of.date())
        else:
            detail += ", not started"
        out.append({"label": "%s %d" % (q, YEAR), "detail": detail})
    return out


class Service:
    """Engine access for the HTTP handler. One lock: pandas work isn't shared
    across threads, and the deal data loads once on first use."""

    def __init__(self, root: str = HERE):
        self.engine = Engine(root, prefer_llm=True)
        self.lock = threading.Lock()

    def meta(self) -> dict:
        as_of = self.engine.manifest.as_of("live")
        return {"as_of": str(as_of.date()) if as_of is not None else None,
                "parser": self.engine.parser.name, "suggestions": SUGGESTIONS,
                "quarters": quarter_status(as_of), "mode": "live"}

    def ask(self, question: str) -> dict:
        question = (question or "").strip()
        if not question:
            return {"kind": "error", "message": "Type a question first."}
        if len(question) > MAX_QUESTION:
            return {"kind": "error",
                    "message": "That question is longer than %d characters." % MAX_QUESTION}
        with self.lock:
            try:
                return serialize.answer_payload(self.engine.ask(question), question,
                                                naive_contrast=is_core_query(question))
            except QueryRejected as exc:
                return serialize.rejection_payload(exc, question, CAPABILITIES)
            except DataIntegrityError as exc:
                return {"kind": "data_error", "question": question,
                        "message": "%s Nothing was answered." % exc}

    def audit(self) -> dict:
        with self.lock:
            try:
                return serialize.audit_payload(self.engine.snaps)
            except DataIntegrityError as exc:
                return {"kind": "data_error", "message": "%s Nothing was answered." % exc}


def make_handler(service: Service, password: str = None):
    class Handler(BaseHTTPRequestHandler):
        def _authorised(self) -> bool:
            if not password:
                return True
            header = self.headers.get("Authorization") or ""
            if header.startswith("Basic "):
                try:
                    supplied = base64.b64decode(header[6:]).decode("utf-8").split(":", 1)[-1]
                except (ValueError, UnicodeDecodeError):
                    supplied = ""
                if hmac.compare_digest(supplied.encode(), password.encode()):
                    return True
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="Meridian", charset="UTF-8"')
            self.send_header("Content-Length", "0")
            self.end_headers()
            return False

        def _send(self, status: int, body: bytes, content_type: str):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, payload, status: int = 200):
            self._send(status, serialize.dumps(payload).encode("utf-8"),
                       "application/json; charset=utf-8")

        def do_GET(self):
            if not self._authorised():
                return
            path = self.path.split("?", 1)[0]
            if path in ("/", "/index.html"):
                with open(PAGE, "rb") as fh:
                    self._send(200, fh.read(), "text/html; charset=utf-8")
            elif path == "/api/meta":
                self._json(service.meta())
            elif path == "/api/audit":
                self._json(service.audit())
            else:
                self._json({"kind": "error", "message": "Not found."}, 404)

        def do_POST(self):
            if not self._authorised():
                return
            if self.path.split("?", 1)[0] != "/api/ask":
                self._json({"kind": "error", "message": "Not found."}, 404)
                return
            try:
                length = min(int(self.headers.get("Content-Length") or 0), 10000)
                body = json.loads(self.rfile.read(length) or b"{}")
                question = body.get("question", "") if isinstance(body, dict) else ""
            except (ValueError, TypeError):
                self._json({"kind": "error", "message": "The request wasn't valid JSON."}, 400)
                return
            try:
                self._json(service.ask(str(question)))
            except Exception as exc:  # show the failure rather than a blank page
                self._json({"kind": "error", "question": question,
                            "message": "The engine failed on this question (%s: %s). "
                                       "Nothing was answered." % (type(exc).__name__, exc)}, 500)

        def log_message(self, fmt, *args):
            pass

    return Handler


def export(path: str, service: Service) -> None:
    """Write a self-contained page with every suggested question pre-answered."""
    recorded = {"meta": dict(service.meta(), mode="recorded", recorded_on=str(date.today())),
                "audit": service.audit(), "answers": {}}
    for group in SUGGESTIONS:
        for q in group["questions"]:
            recorded["answers"][q] = service.ask(q)
    with open(PAGE) as fh:
        page = fh.read()
    blob = serialize.dumps(recorded).replace("</", "<\\/")
    page = page.replace("<script>/*RECORDED*/</script>",
                        "<script>window.MERIDIAN_RECORDED = %s;</script>" % blob)
    with open(path, "w") as fh:
        fh.write(page)


def main(argv) -> int:
    args = argv[1:]
    port = int(os.environ.get("PORT", 8765))
    if "--port" in args:
        port = int(args[args.index("--port") + 1])
    host = args[args.index("--host") + 1] if "--host" in args else "127.0.0.1"
    try:
        service = Service()
    except DataIntegrityError as exc:
        print("Can't start: %s" % exc)
        return 1

    if "--export" in args:
        out = args[args.index("--export") + 1]
        export(out, service)
        print("Wrote %s (recorded run: %d questions)." % (
            out, sum(len(g["questions"]) for g in SUGGESTIONS)))
        return 0

    password = os.environ.get("MERIDIAN_PASSWORD") or None
    server = ThreadingHTTPServer((host, port), make_handler(service, password))
    url = "http://%s:%d/" % ("127.0.0.1" if host == "0.0.0.0" else host, port)
    print("Meridian pipeline intelligence: %s  (parser: %s, password %s, Ctrl-C to stop)"
          % (url, service.engine.parser.name, "on" if password else "off"), flush=True)
    if "--no-browser" not in args:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print()
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
