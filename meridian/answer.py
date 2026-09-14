"""Rendering.

Every answer prints six sections, in this order:

    ANSWER              the direct answer, how the question was read, flag count
    SUPPORTING METRICS  the figures around it
    SOURCE DATA         file, snapshot date, filters and rows for each dataset
    METHOD              the validated query, then each formula with its numbers
    ASSUMPTIONS         the definitions the answer depends on
    TRUST FLAGS         what a reader must know about this answer

The ANSWER section always states how many flags were raised and how severe, so
nothing critical sits unseen at the bottom.
"""
from __future__ import annotations

import json
import sys
from typing import List

from . import audit
from .model import Answer, Calculation, Table, cell, money, pct, plain, times

_TTY = sys.stdout.isatty()


def _c(code: str, text: str) -> str:
    return "\033[%sm%s\033[0m" % (code, text) if _TTY else text


BOLD = lambda s: _c("1", s)
DIM = lambda s: _c("2", s)
RED = lambda s: _c("31", s)
YEL = lambda s: _c("33", s)
CYN = lambda s: _c("36", s)
GRN = lambda s: _c("32", s)

LEVEL_STYLE = {"critical": (RED, "CRITICAL"), "warning": (YEL, "WARNING"),
               "context": (CYN, "CONTEXT")}
LEVEL_ORDER = {"critical": 0, "warning": 1, "context": 2}
RULE = "-" * 78
TABLE_ROW_LIMIT = 12


def wrap(text: str, width: int = 76, indent: str = "  ", first: str = None) -> str:
    first = indent if first is None else first
    words, lines, cur = plain(text).split(), [], ""
    for w in words:
        room = width - len(_strip_ansi(first if not lines else indent))
        if cur and len(cur) + len(w) + 1 > room:
            lines.append(cur)
            cur = w
        else:
            cur = (cur + " " + w).strip()
    lines.append(cur)
    return "\n".join((first if i == 0 else indent) + l for i, l in enumerate(lines))


def _strip_ansi(text: str) -> str:
    import re
    return re.sub(r"\033\[[0-9;]*m", "", text)


def header(title: str) -> str:
    return "\n" + BOLD(title)


def _fmt(value, fmt: str, lower: bool = False) -> str:
    if fmt == "pct":
        return pct(value, 2, lower)
    if fmt == "x":
        return times(value, 4, lower)
    return money(value, lower)


# ---------------------------------------------------------------------------
# sections
# ---------------------------------------------------------------------------

def _answer(ans: Answer) -> List[str]:
    out = [header("ANSWER"),
           wrap(ans.query.describe(), indent="           ", first="  " + CYN("Read as: ")),
           ""]
    out += [BOLD(line) for line in wrap(ans.headline).split("\n")]
    for line in ans.lines:
        if line.startswith("- "):
            out.append(wrap(line[2:], indent="      ", first="    - "))
        else:
            out.append(wrap(line))
    counts = {}
    for f in ans.flags:
        counts[f.level] = counts.get(f.level, 0) + 1
    if counts:
        parts = ["%d %s" % (counts[l], l if counts[l] == 1 or l == "context" else l + "s")
                 for l in ("critical", "warning", "context") if l in counts]
        summary = "Flags raised: %s. Details under TRUST FLAGS." % ", ".join(parts)
        out += ["", "  " + (RED(summary) if "critical" in counts else YEL(summary))]
    else:
        out += ["", "  " + DIM("Flags raised: none.")]
    return out


def render_table(table: Table, indent: str = "  ", limit=None) -> List[str]:
    rows = table.rows if limit is None else table.rows[:limit]
    widths = [max([len(c)] + [len(str(r[i])) for r in rows]) for i, c in enumerate(table.columns)]

    def cell(value, i):
        col = table.columns[i]
        return str(value).rjust(widths[i]) if col in table.numeric else str(value).ljust(widths[i])

    out = [indent + BOLD(table.title),
           indent + DIM("  ".join(cell(c, i) for i, c in enumerate(table.columns)))]
    for r in rows:
        out.append((indent + "  ".join(cell(v, i) for i, v in enumerate(r))).rstrip())
    if limit is not None and len(table.rows) > limit:
        out.append(indent + DIM("... %d more rows; run with --full to print all %d"
                                % (len(table.rows) - limit, len(table.rows))))
    return out


def _metrics(ans: Answer) -> List[str]:
    out = [header("SUPPORTING METRICS")]
    if ans.metrics:
        width = max(len(m.label) for m in ans.metrics)
        vwidth = max(len(m.value) for m in ans.metrics)
        for m in ans.metrics:
            line = "  %s  %s" % (m.label.ljust(width), m.value.rjust(vwidth))
            if m.note:
                line += "  " + DIM(m.note)
            out.append(line)
    for table in ans.tables:
        out.append("")
        out += render_table(table)
    return out


def _deal_rows_table(sel, full: bool) -> List[str]:
    rows = []
    for _, r in sel.rows.sort_values("_line").iterrows():
        rows.append([str(r._line), r.deal_id, cell(r.account_name), r.rep_id, r.segment, r.stage,
                     money(r.deal_value), str(r.close_date.date())])
    table = Table("Rows in [%s]" % sel.key,
                  ["Line", "Deal", "Account", "Rep", "Segment", "Stage", "Value", "Close date"],
                  rows, numeric=["Line", "Value"])
    return render_table(table, indent="      ", limit=None if full else TABLE_ROW_LIMIT)


def _source(ans: Answer, full: bool) -> List[str]:
    out = [header("SOURCE DATA")]
    listed_elsewhere = ans.intent == "deal_list"

    def field(key, value):
        return wrap(value, indent=" " * 16, first="      %-10s" % key)

    for sel in ans.datasets:
        out.append("")
        out.append("  " + BOLD("[%s] %s" % (sel.key, sel.title)))
        out.append(field("File", sel.file))
        out.append(field("Snapshot", sel.snapshot.describe()))
        filters = [f.text for f in sel.filters] or ["none (every row)"]
        out.append(field("Filters", filters[0]))
        for f in filters[1:]:
            out.append(" " * 16 + f)
        if sel.kind == "deals":
            ids = ", ".join("%s (line %d)" % (r.deal_id, r._line)
                            for _, r in sel.rows.sort_values("_line").iterrows()) or "none"
            out.append(field("Rows", "%d of %d: %s" % (sel.count, sel.rows_in_file, ids)))
            out.append(field("Total", "sum(deal_value) = %s" % money(sel.total, not sel.complete)))
            if sel.complete:
                out.append(field("Uncounted", "none"))
            else:
                out.append(field("Uncounted", "; ".join(
                    "%s (line %d: %s)" % (r.deal_id if isinstance(r.deal_id, str) else "unnamed row",
                                          r._line, r._reason)
                    for _, r in sel.unplaced.iterrows())))
            if sel.count and not listed_elsewhere:
                out += _deal_rows_table(sel, full)
        else:
            ids = "; ".join("%s %s %s (line %d)" % (r.rep_id, cell(r.rep_name), money(r[sel.column]), r._line)
                            for _, r in sel.rows.iterrows()) or "none"
            out.append(field("Rows", "%d of %d reps: %s" % (len(sel.rows), sel.rows_in_file, ids)))
            out.append(field("Total", "sum(%s) = %s" % (
                sel.column, money(sel.total) if sel.total is not None
                else "undefined (%s)" % sel.undefined_reason)))
            if not sel.missing.empty:
                out.append(field("Missing", "; ".join(
                    "%s (line %d): %s is %s" % (
                        r.rep_id if isinstance(r.rep_id, str) else "unnamed rep", r._line, sel.column,
                        "blank" if str(r["_raw_" + sel.column]).strip() == ""
                        else repr(r["_raw_" + sel.column]))
                    for _, r in sel.missing.iterrows())))
    return out


def _calc_lines(i: int, calc: Calculation) -> List[str]:
    lead = "  %d. " % i
    pad = " " * len(lead)
    name = "%s = " % calc.name
    out = [wrap(calc.formula, indent=pad + "    ", first=lead + name)]
    num, den = calc.numerator, calc.denominator
    eq = pad + "  = "
    if calc.op == "sum":
        out.append(eq + _fmt(num.value, num.fmt, num.lower_bound))
    else:
        out.append(eq + "%s %s %s" % (_fmt(num.value, num.fmt, num.lower_bound), calc.op,
                                      _fmt(den.value, den.fmt, den.lower_bound)))
        out.append(eq + calc.display(4 if calc.kind == "x" else 2))
    out.append(wrap("%s = %s: %s" % (num.label, _fmt(num.value, num.fmt, num.lower_bound),
                                     num.explanation),
                    indent=pad + " " * 15,
                    first=pad + DIM({"/": "numerator:     ", "-": "from:          "}.get(calc.op, "total:         "))))
    if den is not None:
        out.append(wrap("%s = %s: %s" % (den.label, _fmt(den.value, den.fmt, den.lower_bound),
                                         den.undefined_reason or den.explanation),
                        indent=pad + " " * 15,
                        first=pad + DIM("denominator:   " if calc.op == "/" else "subtracted:    ")))
    return out


def _method(ans: Answer) -> List[str]:
    out = [header("METHOD"),
           "  1. The question was turned into this query and validated against the",
           "     roster and snapshot manifest before any deal data was read:"]
    out += ["       " + l for l in json.dumps(ans.query.as_dict(), indent=2).splitlines()]
    out.append("  2. Rows were selected from the files under SOURCE DATA, using exactly the")
    out.append("     filters listed there. Rows that failed a check on load were never counted.")
    for i, calc in enumerate(ans.calculations, start=3):
        out.append("")
        out += _calc_lines(i, calc)
    return out


def _assumptions(ans: Answer) -> List[str]:
    out = [header("ASSUMPTIONS")]
    for a in ans.assumptions:
        out.append(wrap(a.text, indent="    ", first="  - "))
    return out


def _flags(ans: Answer) -> List[str]:
    if not ans.flags:
        return [header("TRUST FLAGS"), "  " + DIM("none raised")]
    out = [header("TRUST FLAGS  (%d)" % len(ans.flags))]
    for f in sorted(ans.flags, key=lambda x: LEVEL_ORDER[x.level]):
        paint, label = LEVEL_STYLE[f.level]
        out.append("  " + paint("! " + label) + "  " + BOLD(f.code))
        out.append(wrap(f.message, indent="     "))
        out.append("")
    return out


def render(ans: Answer, full: bool = False) -> str:
    if ans.intent == "audit":
        return render_audit(ans.audit_snaps)
    lines = [RULE]
    for section in (_answer(ans), _metrics(ans), _source(ans, full), _method(ans),
                    _assumptions(ans), _flags(ans)):
        lines += section
    lines.append(RULE)
    return "\n".join(lines)


def render_rejection(exc, capabilities: str) -> str:
    out = [RULE, header("NO ANSWER"),
           "  This question isn't answered as asked, and nothing was computed.", ""]
    for p in exc.problems:
        out.append("  " + RED("! ") + BOLD(p.code))
        out.append(wrap(p.message, indent="     "))
        out.append("")
    if exc.raw is not None:
        understood = {k: v for k, v in exc.raw.as_dict().items() if v}
        out.append(wrap(json.dumps(understood), indent="     ",
                        first="  " + DIM("Understood so far: ")))
    out.append(wrap(capabilities))
    out.append(RULE)
    return "\n".join(out)


def render_data_error(exc) -> str:
    return "\n".join([RULE, header("DATA ERROR"),
                      wrap("%s Nothing was answered." % exc), RULE])


# ---------------------------------------------------------------------------
# the audit report
# ---------------------------------------------------------------------------

def render_audit(snaps) -> str:
    rep = audit.integrity_report(snaps)
    bridge = rep["bridge"]
    at_close = audit.q1_bookings(snaps.at_q1_close)
    live = audit.q1_bookings(snaps.live)
    out = [RULE, header("DATA INTEGRITY AUDIT"),
           "  Meridian's two extracts disagree about a quarter they consider",
           "  closed. Both numbers below are computed from their own files.",
           ""]
    out.append("  " + BOLD("Q1 2026 bookings, per each extract"))
    out.append("    %-40s %12s   %d deals" % ("at Q1 close (%s)" % snaps.at_q1_close.deals_file,
                                             money(at_close.total), at_close.deal_count))
    out.append("    %-40s %12s   %d deals" % ("live, %s (%s)" % (snaps.live.as_of.date(),
                                                                  snaps.live.deals_file),
                                             money(live.total), live.deal_count))
    out.append("    " + RED("%-40s %12s" % ("difference", money(bridge.closing - bridge.opening))))
    out.append("")
    out.append("  " + BOLD("Reconciliation"))
    out.append("    %-38s %14s" % ("Q1 bookings at close", money(bridge.opening)))
    for line in bridge.lines:
        out.append("    %-38s %14s  %s" % (line.label, money(line.amount), DIM(
            "%d deal%s" % (line.deal_count, "" if line.deal_count == 1 else "s"))))
    out.append("    " + DIM("-" * 58))
    out.append("    %-38s %14s" % ("Q1 bookings per live extract", money(bridge.closing)))
    status = (GRN("balances to the dollar") if bridge.unexplained == 0
              else RED("UNEXPLAINED: %s" % money(bridge.unexplained)))
    out.append("    %-38s %14s" % ("unexplained", status))
    out.append("")
    out.append("  " + BOLD("Findings"))
    shared = len(set(snaps.at_q1_close.clean.deal_id) & set(snaps.live.clean.deal_id))
    out.append("    %-52s %s" % ("deal records changed between extracts",
                                 "%d of %d" % (rep["changed_deals"], shared)))
    out.append("    %-52s %s" % ("deal IDs recycled onto different accounts", len(rep["recycled"])))
    out.append(wrap(", ".join(rep["recycled"]), indent="      "))
    out.append("    %-52s %s" % ("deals whose region conflicts with rep territory",
                                 rep["conflicts"]["region"]))
    seg = rep["conflicts"]["segment"]
    out.append("    %-52s %s" % ("deals whose segment conflicts with rep segment",
                                 "0  (segment rollups are safe)" if seg == 0 else seg))
    out.append("    %-52s %s" % ("open deals with a close date already past",
                                 ", ".join(rep["stale"].deal_id) or "none"))
    out.append("    %-52s %s" % ("genuinely new deal IDs in the live extract",
                                 ", ".join(rep["new_ids"]) or "none"))
    for snap in (snaps.at_q1_close, snaps.live):
        rows = snap.deals[snap.deals._problem.notna()]
        out.append("    %-52s %s" % ("rows failing checks in %s" % snap.deals_file,
                                     len(rows) or "none"))
        for _, r in rows.iterrows():
            out.append(wrap("line %d: %s" % (r._line, r._problem), indent="        "))
    out.append("")
    out.append("  " + BOLD("What this means"))
    out.append(wrap(
        "Neither Q1 number is wrong on its own. %s is what Meridian's CRM said at "
        "quarter close; %s is what it says today. The difference comes down to %d "
        "specific, nameable causes. Until a snapshot policy exists, every dashboard "
        "reading these files will disagree, and no reporting layer on top can fix that."
        % (money(bridge.opening), money(bridge.closing), len(bridge.lines)), indent="    "))
    out.append("")
    out.append(RULE)
    return "\n".join(out)
