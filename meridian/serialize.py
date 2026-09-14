"""JSON for the browser app.

Every figure is formatted here, by the same helpers the terminal renderer uses.
The page never formats, rounds or computes a number itself, so the browser and
the terminal can't disagree.

Output is written with allow_nan=False: a NaN that slipped through anywhere
raises instead of reaching the page.
"""
from __future__ import annotations

import json

from . import audit as audit_mod
from . import naive
from .answer import _fmt
from .data import LOST
from .model import Answer, cell, money, pct, times


def dumps(payload) -> str:
    return json.dumps(payload, allow_nan=False)


def answer_payload(ans: Answer, question: str, naive_contrast: bool = True) -> dict:
    if ans.intent == "audit":
        return audit_payload(ans.audit_snaps)
    counts = {"critical": 0, "warning": 0, "context": 0}
    for f in ans.flags:
        counts[f.level] += 1
    return {
        "kind": "answer",
        "question": question,
        "intent": ans.intent,
        "read_as": ans.query.describe(),
        "headline": ans.headline,
        "lines": ans.lines,
        "flag_counts": counts,
        "metrics": [{"label": m.label, "value": m.value, "note": m.note} for m in ans.metrics],
        "tables": [_table(t) for t in ans.tables],
        "datasets": [_dataset(d) for d in ans.datasets],
        "method": {
            "query": ans.query.as_dict(),
            "calculations": [_calculation(c) for c in ans.calculations],
        },
        "assumptions": [{"code": a.code, "text": a.text} for a in ans.assumptions],
        "flags": [{"code": f.code, "level": f.level, "message": f.message,
                   "deal_ids": f.deal_ids}
                  for f in sorted(ans.flags, key=lambda f: ("critical", "warning", "context").index(f.level))],
        "naive": naive.build(ans) if naive_contrast else None,
        "rep_chart": rep_chart(ans),
    }


# Coverage thresholds the rep chart's slider steps through, in twentieths: 0.50x to 1.30x.
THRESHOLD_STEPS = list(range(10, 27))
DEFAULT_STEP = 20


def rep_chart(ans: Answer):
    """Every figure the rep chart shows, at every threshold the slider can reach.

    The page only picks a column and draws bars; it formats nothing. At 1.00x the
    counts match the answer's at-risk verdicts, which always use 1.00x.
    """
    if ans.intent != "rep_risk" or ans.query.rep_id or "records" not in ans.values:
        return None
    records = ans.values["records"]
    reps, counts = [], [0] * len(THRESHOLD_STEPS)
    unknown = [0] * len(THRESHOLD_STEPS)
    scale = 0
    for r in records:
        q, combined = r["quota"], r["won"] + r["open"]
        uncounted = r["verdict"] == "unknown: rows uncounted"
        defined = bool(q)
        scale = max(scale, combined, q * THRESHOLD_STEPS[-1] / 20.0 if q else 0)
        gaps, gap_values, status = [], [], []
        for i, k in enumerate(THRESHOLD_STEPS):
            if not defined:
                gaps.append("unknown"); gap_values.append(None); status.append("unknown")
                unknown[i] += 1
                continue
            gap = q * k / 20.0 - combined
            below = combined * 20 < q * k
            if below and uncounted:
                st = "unknown"
                unknown[i] += 1
            else:
                st = "below" if below else "meets"
                counts[i] += st == "below"
            gaps.append(money(gap, uncounted)); gap_values.append(gap); status.append(st)
        reps.append({
            "rep_id": r["rep_id"], "name": r["name"], "segment": r["segment"],
            "won": money(r["won"]), "open": money(r["open"]), "quota": money(q),
            "won_n": "%d deal%s" % (r["won_n"], "" if r["won_n"] == 1 else "s"),
            "open_n": "%d deal%s" % (r["open_n"], "" if r["open_n"] == 1 else "s"),
            "won_v": r["won"], "open_v": r["open"], "quota_v": q,
            "coverage": times(r["coverage"]), "coverage_v": r["coverage"],
            "reached": pct(r["won"] / q if q else None), "reached_v": r["won"] / q if q else None,
            "gaps": gaps, "gap_v": gap_values, "status": status,
            "targets": [money(q * k / 20.0) if defined else "unknown" for k in THRESHOLD_STEPS],
        })
    labels = ["%.2fx" % (k / 20.0) for k in THRESHOLD_STEPS]
    headings = []
    for i, label in enumerate(labels):
        h = "%d of %d reps are below %s coverage" % (counts[i], len(records), label)
        if unknown[i]:
            h += "; %d can't be assessed" % unknown[i]
        headings.append(h)
    return {"thresholds": labels, "factors": [k / 20.0 for k in THRESHOLD_STEPS], "default": THRESHOLD_STEPS.index(DEFAULT_STEP),
            "headings": headings, "below": counts, "scale": scale, "reps": reps}


def rejection_payload(exc, question: str, capabilities: str) -> dict:
    understood = {}
    if exc.raw is not None:
        understood = {k: v for k, v in exc.raw.as_dict().items() if v}
    return {
        "kind": "rejection",
        "question": question,
        "problems": [{"code": p.code, "message": p.message} for p in exc.problems],
        "understood": understood,
        "capabilities": capabilities,
    }


def _table(table) -> dict:
    return {"title": table.title, "columns": table.columns, "rows": table.rows,
            "numeric": sorted(table.numeric)}


def _calculation(calc) -> dict:
    num, den = calc.numerator, calc.denominator
    if calc.op == "sum":
        steps = ["= " + _fmt(num.value, num.fmt, num.lower_bound)]
    else:
        steps = ["= %s %s %s" % (_fmt(num.value, num.fmt, num.lower_bound), calc.op,
                                 _fmt(den.value, den.fmt, den.lower_bound)),
                 "= " + calc.display(4 if calc.kind == "x" else 2)]
    terms = [{"role": {"/": "numerator", "-": "from", "sum": "total"}[calc.op],
              "label": num.label, "value": _fmt(num.value, num.fmt, num.lower_bound),
              "explanation": num.explanation}]
    if den is not None:
        terms.append({"role": "denominator" if calc.op == "/" else "subtracted",
                      "label": den.label, "value": _fmt(den.value, den.fmt, den.lower_bound),
                      "explanation": den.undefined_reason or den.explanation})
    return {"name": calc.name, "formula": calc.formula, "steps": steps,
            "result": calc.display(4 if calc.kind == "x" else 2),
            "defined": calc.result is not None, "terms": terms}


def _dataset(sel) -> dict:
    snap = sel.snapshot
    base = {
        "key": sel.key, "title": sel.title, "kind": sel.kind, "file": sel.file,
        "snapshot": {"label": snap.describe(),
                     "as_of": str(snap.as_of.date()) if snap.as_of is not None else None,
                     "basis": snap.as_of_basis},
        "filters": [f.text for f in sel.filters],
        "rows_in_file": sel.rows_in_file,
    }
    if sel.kind == "deals":
        rows = sel.rows.sort_values("_line")
        show_loss = bool((rows.stage == LOST).any())
        columns = ["Line", "Deal", "Account", "Rep", "Segment", "Stage", "Value", "Close date"]
        if show_loss:
            columns.append("Loss reason")
        out = []
        for _, r in rows.iterrows():
            line = [str(r._line), r.deal_id, cell(r.account_name), r.rep_id, r.segment, r.stage,
                    money(r.deal_value), str(r.close_date.date())]
            if show_loss:
                line.append(("MISSING" if cell(r.loss_reason) == "(blank)" else r.loss_reason)
                            if r.stage == LOST else "")
            out.append(line)
        base.update(
            count=sel.count, complete=sel.complete, columns=columns, rows=out,
            numeric=["Line", "Value"], ids=list(rows.deal_id),
            total=money(sel.total, not sel.complete),
            total_expr="sum(deal_value) = %s" % money(sel.total, not sel.complete),
            uncounted=[{"id": cell(r.deal_id), "line": int(r._line), "reason": r._reason}
                       for _, r in sel.unplaced.iterrows()])
    else:
        columns = ["Line", "Rep", "Name", "Segment", sel.column]
        out = [[str(r._line), r.rep_id, cell(r.rep_name), cell(r.segment), money(r[sel.column])]
               for _, r in sel.rows.iterrows()]
        total = money(sel.total) if sel.total is not None else "undefined"
        base.update(
            count=len(sel.rows), complete=sel.missing.empty, columns=columns, rows=out,
            numeric=["Line", sel.column], ids=list(sel.rows.rep_id),
            total=total,
            total_expr="sum(%s) = %s" % (sel.column, total if sel.total is not None
                                         else "undefined (%s)" % sel.undefined_reason),
            uncounted=[{"id": cell(r.rep_id), "line": int(r._line),
                        "reason": "%s is %s" % (sel.column, "blank"
                                                if str(r["_raw_" + sel.column]).strip() == ""
                                                else repr(r["_raw_" + sel.column]))}
                       for _, r in sel.missing.iterrows()])
    return base


def _short(amount: int, signed: bool = False) -> str:
    """Compact label for the chart: $6.04M, -$1,220K."""
    sign = "-" if amount < 0 else ("+" if signed and amount > 0 else "")
    value = abs(amount)
    text = "$%.2fM" % (value / 1e6) if value >= 1_000_000 and not signed else "${:,}K".format(int(round(value / 1000)))
    return sign + text


def _deal_fields(row) -> dict:
    if row is None:
        return None
    return {"account": cell(row.account_name), "stage": cell(row.stage),
            "close": str(row.close_date.date()), "value": money(row.deal_value)}


def _compare(did: str, before, after) -> dict:
    was = _deal_fields(before.loc[did]) if before is not None and did in before.index else None
    now = _deal_fields(after.loc[did]) if after is not None and did in after.index else None
    changed = [k for k in ("account", "stage", "close", "value")
               if was and now and was[k] != now[k]]
    return {"id": did, "before": was, "after": now, "changed": changed}


_BRIDGE_NOTES = {
    "recycled_id": ("Deal IDs recycled onto new Q2 deals", "Recycled IDs", "records destroyed"),
    "slipped_to_q2": ("Close date slipped into Q2", "Slipped to Q2", "still won, different quarter"),
    "reverted_to_open": ("Reverted to open pipeline", "Reverted to open", "un-won"),
    "flipped_to_lost": ("Flipped to Closed Lost", "Flipped to lost", "now Closed Lost"),
    "value_revised": ("Deal value revised", "Value revised", "value changed"),
}


def _plural(n: int, word: str) -> str:
    return "%d %s%s" % (n, word, "" if n == 1 else "s")


def audit_payload(snaps) -> dict:
    close, live = snaps.at_q1_close, snaps.live
    report = audit_mod.integrity_report(snaps)
    bridge = report["bridge"]
    at_close = audit_mod.q1_bookings(close)
    in_live = audit_mod.q1_bookings(live)
    a = close.clean.set_index("deal_id")
    b = live.clean.set_index("deal_id")
    gap = bridge.closing - bridge.opening

    lines = [{
        "id": "opening", "kind": "total", "label": "Q1 bookings per the Q1 extract",
        "note": _plural(at_close.deal_count, "Closed-Won deal"), "amount": money(bridge.opening),
        "view": "before", "deals": [_compare(d, a, None) for d in sorted(at_close.rows.deal_id)]}]
    for i, line in enumerate(bridge.lines):
        label, _, note = _BRIDGE_NOTES[line.cause]
        if line.cause == "value_revised":
            label = "Deal value revised down" if line.amount < 0 else "Deal value revised up"
            note = "value lowered" if line.amount < 0 else "value raised"
        lines.append({
            "id": "step-%d" % i, "kind": "decrease" if line.amount < 0 else "increase",
            "label": label, "note": "%s · %s" % (_plural(line.deal_count, "deal"), note),
            "amount": money(line.amount), "view": "compare",
            "deals": [_compare(d, a, b) for d in line.deal_ids]})
    lines.append({
        "id": "closing", "kind": "total", "label": "Q1 bookings per the Q2 extract",
        "note": _plural(in_live.deal_count, "Closed-Won deal"), "amount": money(bridge.closing),
        "view": "after", "deals": [_compare(d, None, b) for d in sorted(in_live.rows.deal_id)]})

    # Chart geometry: where each bar starts and ends, in dollars.
    chart, running = [], bridge.opening
    chart.append({"line": "opening", "kind": "total", "axis": "Q1 extract", "label": _short(bridge.opening),
                  "start": 0, "end": bridge.opening, "tooltip": "Q1 bookings per the Q1 extract: %s" % money(bridge.opening)})
    for i, line in enumerate(bridge.lines):
        _, axis, _ = _BRIDGE_NOTES[line.cause]
        chart.append({"line": "step-%d" % i, "kind": "decrease" if line.amount < 0 else "increase",
                      "axis": axis, "label": _short(line.amount, signed=True),
                      "start": running, "end": running + line.amount,
                      "tooltip": "%s: %s across %s" % (lines[i + 1]["label"], money(line.amount),
                                                       _plural(line.deal_count, "deal"))})
        running += line.amount
    chart.append({"line": "closing", "kind": "total", "axis": "Q2 extract", "label": _short(bridge.closing),
                  "start": 0, "end": bridge.closing, "tooltip": "Q1 bookings per the Q2 extract: %s" % money(bridge.closing)})

    # Beyond the bridge.
    beyond = []
    recycled = report["recycled"]
    if recycled:
        ex = recycled[0]
        recycled_q1 = next((l for l in bridge.lines if l.cause == "recycled_id"), None)
        beyond.append({
            "severity": "high", "title": "Deal IDs recycled onto unrelated Q2 deals",
            "text": "%s point at completely different accounts in the two extracts. %s, for example, is %s, a %s %s, in the Q1 extract and %s, a %s deal, in the live extract.%s" % (
                _plural(len(recycled), "deal ID"), ex, cell(a.loc[ex].account_name), money(a.loc[ex].deal_value),
                "Q1 win" if a.loc[ex].stage == "Closed Won" else a.loc[ex].stage,
                cell(b.loc[ex].account_name), cell(b.loc[ex].stage),
                (" %s of booked Q1 revenue (%s) has no surviving record under its original ID." % (
                    money(-recycled_q1.amount), _plural(recycled_q1.deal_count, "deal")) if recycled_q1 else "")),
            "view": "compare", "deals": [_compare(d, a, b) for d in recycled]})
    losses = audit_mod.reopened_losses(close, live)
    if losses:
        reopened = [d for d, kind in losses if kind == "reopened"]
        erased = [d for d, kind in losses if kind == "reason erased"]
        parts = []
        if reopened:
            parts.append("%s moved back to open pipeline with the loss reason erased (%s)" % (
                _plural(len(reopened), "deal"), ", ".join(reopened)))
        if erased:
            parts.append("%s kept Closed Lost but lost %s reason (%s)" % (
                _plural(len(erased), "more deal" if reopened else "deal"),
                "its" if len(erased) == 1 else "their", ", ".join(erased)))
        beyond.append({
            "severity": "high", "title": "Closed-Lost deals reopened or lost their reason",
            "text": "; ".join(parts) + ". Win/loss analysis isn't reliable across the two extracts.",
            "view": "compare", "compare_fields": ["stage", "close", "loss"],
            "deals": [dict(_compare(d, a, b), loss_before=cell(a.loc[d].loss_reason),
                           loss_after=cell(b.loc[d].loss_reason)) for d, _ in losses]})
    regions = audit_mod.region_conflicts(live)
    if len(regions):
        beyond.append({
            "severity": "ambiguity", "title": "Deal region and rep territory disagree",
            "text": "%s in the live extract sit in a different region than the rep's own territory. Region questions are refused until a basis is chosen." % _plural(len(regions), "deal"),
            "view": "table", "columns": ["Deal", "Account", "Rep", "Deal region", "Rep territory"],
            "rows": [[r.deal_id, cell(r.account_name), "%s %s" % (r.rep_id, cell(r.rep_name)),
                      cell(r.region_deal), cell(r.region_rep)] for _, r in regions.iterrows()],
            "ids": list(regions.deal_id)})
    stale = report["stale"]
    if len(stale):
        beyond.append({
            "severity": "minor", "title": "Deals past their own close date",
            "text": "%s carry a close date before the snapshot date (%s) and were never updated: stale forecast entries." % (
                _plural(len(stale), "open deal"), live.as_of.date()),
            "view": "table", "columns": ["Deal", "Account", "Rep", "Stage", "Value", "Close date"],
            "rows": [[r.deal_id, cell(r.account_name), r.rep_id, r.stage, money(r.deal_value),
                      str(r.close_date.date())] for _, r in stale.sort_values("deal_id").iterrows()],
            "ids": list(stale.deal_id)})
    problems = []
    for snap in (close, live):
        for _, r in snap.deals[snap.deals._problem.notna()].iterrows():
            problems.append([snap.deals_file, str(int(r._line)), cell(r.deal_id), r._problem])
    if problems:
        beyond.append({
            "severity": "high", "title": "Rows that failed checks on load",
            "text": "%s can't be counted anywhere and are listed in every answer they could affect." % _plural(len(problems), "row"),
            "view": "table", "columns": ["File", "Line", "Deal", "Problem"], "rows": problems, "ids": []})

    return {
        "kind": "audit",
        "title": "The %s Bridge" % ("$%.2f M" % (abs(gap) / 1e6)),
        "headline": "Q1 2026 bookings depend on which extract you open",
        "extracts": [
            {"label": "Extract taken at Q1 close", "file": close.deals_file,
             "as_of": str(close.as_of.date()), "basis": close.as_of_basis,
             "total": money(at_close.total), "count": at_close.deal_count},
            {"label": "Live extract", "file": live.deals_file,
             "as_of": str(live.as_of.date()), "basis": live.as_of_basis,
             "total": money(in_live.total), "count": in_live.deal_count},
        ],
        "difference": money(abs(gap)),
        "balanced": bridge.unexplained == 0,
        "unexplained": money(bridge.unexplained),
        "chart": chart,
        "lines": lines,
        "beyond": beyond,
    }
