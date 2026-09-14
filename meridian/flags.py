"""Flags and assumptions are output, not documentation.

Assumptions are the definitions an answer depends on: what "this quarter"
means, what counts as "at risk". They are printed on every answer that uses
them.

Flags are things specific to this answer that a reader must know: a deal that
moved quarters, a missing quota, an ambiguous phrase. Each one is raised at the
point in the code where the issue is detected.
"""
from __future__ import annotations

from typing import List, Optional

LEVELS = ("critical", "warning", "context")


class Flag:
    def __init__(self, code: str, level: str, message: str,
                 deal_ids: Optional[List[str]] = None):
        if level not in LEVELS:
            raise ValueError("unknown flag level %r" % level)
        self.code = code
        self.level = level
        self.message = message
        self.deal_ids = sorted(deal_ids) if deal_ids else []

    def __repr__(self):
        return "<Flag %s %s>" % (self.level, self.code)


class Assumption:
    def __init__(self, code: str, text: str):
        self.code = code
        self.text = text

    def __repr__(self):
        return "<Assumption %s>" % self.code
