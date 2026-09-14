"""Validation: a raw query becomes a checked, fully resolved query, or it is
rejected with every problem named.

This reads the rep roster and the snapshot manifest, and nothing else. No deal
row has been read at this point, so nothing about the answer can shape how the
question is interpreted.

Rejected: unknown segments, unknown or ambiguous rep names, managers named as
reps, unsupported periods, snapshots with no as-of date, and combinations no
metric supports.

Flagged, and answered with the interpretation printed: ambiguous phrases such
as "on track" and "same point".
"""
from __future__ import annotations

import re
from typing import List, Optional, Tuple

import pandas as pd

from .data import (KNOWN_STAGES, LOST, OPEN_STAGES, QUARTERS, SEGMENTS, WON,
                   YEAR, Manifest, Roster, day_of_quarter, quarter_length,
                   quarter_of, same_elapsed_day)
from .flags import Assumption, Flag
from .query import Problem, QueryRejected, RawQuery

SEGMENT_ALIASES = {"enterprise": "Enterprise", "mid-market": "Mid-Market",
                   "smb": "SMB", "small business": "SMB"}
PREVIOUS_QUARTER = {"Q2": "Q1"}
SNAPSHOT_FOR = {"Q1": "at_q1_close", "Q2": "live"}


class ValidatedQuery:
    """Everything needed to run a query, resolved and checked."""

    def __init__(self, raw: RawQuery, intent: str, quarter: str,
                 as_of: pd.Timestamp, through: pd.Timestamp,
                 segment: Optional[str] = None, rep_id: Optional[str] = None,
                 rep_name: Optional[str] = None, stages: Optional[tuple] = None,
                 compare_quarter: Optional[str] = None,
                 compare_through: Optional[pd.Timestamp] = None,
                 day: Optional[int] = None,
                 assumptions: Optional[List[Assumption]] = None,
                 flags: Optional[List[Flag]] = None):
        self.raw = raw
        self.intent = intent
        self.quarter = quarter
        self.as_of = as_of
        self.through = through
        self.segment = segment
        self.rep_id = rep_id
        self.rep_name = rep_name
        self.stages = stages
        self.compare_quarter = compare_quarter
        self.compare_through = compare_through
        self.day = day
        self.assumptions = assumptions or []
        self.flags = flags or []

    @property
    def period(self) -> str:
        return "%s %d" % (self.quarter, YEAR)

    def as_dict(self) -> dict:
        return {
            "intent": self.intent,
            "period": self.period,
            "through": str(self.through.date()),
            "as_of": str(self.as_of.date()),
            "segment": self.segment,
            "rep": "%s (%s)" % (self.rep_id, self.rep_name) if self.rep_id else None,
            "stages": list(self.stages) if self.stages else None,
            "compare_to": ("%s %d through %s (day %d)" % (
                self.compare_quarter, YEAR, self.compare_through.date(), self.day)
                if self.compare_quarter else None),
            "ambiguous_phrases": list(self.raw.phrases),
            "parsed_by": self.raw.source,
        }

    def describe(self) -> str:
        """How the question was read, built from resolved fields only."""
        who = self.rep_name and "%s (%s)" % (self.rep_name, self.rep_id)
        scope = who or (self.segment or "all segments")
        if self.intent == "audit":
            return "data integrity audit of both CRM extracts"
        if self.intent == "attainment":
            if self.through < QUARTERS[self.quarter][1]:
                who_tracks = who or self.segment or "Meridian (all segments)"
                return ("how %s is tracking in %s: bookings, open pipeline and coverage "
                        "against quota, through %s" % (who_tracks, self.period, self.through.date()))
            return "%s bookings against quota, %s, through %s" % (
                self.period, scope, self.through.date())
        if self.intent == "coverage":
            return ("%s pipeline coverage (closed-won plus open pipeline against "
                    "quota), %s, as of %s" % (self.period, scope, self.as_of.date()))
        if self.intent == "rep_risk":
            if who:
                return "whether and why %s is at risk in %s, as of %s" % (
                    who, self.period, self.as_of.date())
            return ("%s reps at risk (closed-won plus open pipeline below quota), "
                    "%s, as of %s" % (self.period, scope, self.as_of.date()))
        if self.intent == "quarter_comparison":
            return "%s through day %d (%s) against %s %d through day %d (%s), %s" % (
                self.period, self.day, self.through.date(), self.compare_quarter,
                YEAR, self.day, self.compare_through.date(), scope)
        if self.intent == "deal_list":
            stages = ("any stage" if not self.stages else
                      "open stages" if tuple(self.stages) == OPEN_STAGES else
                      ", ".join(self.stages))
            who_deals = ("%s's deals" % who if who else
                         "%s deals" % self.segment if self.segment else
                         "deals in all segments")
            return "%s, %s, close date in %s" % (who_deals, stages, self.period)
        return self.intent


class Validator:
    def __init__(self, roster: Roster, manifest: Manifest):
        self.roster = roster
        self.manifest = manifest

    def validate(self, raw: RawQuery) -> ValidatedQuery:
        problems: List[Problem] = list(raw.problems)
        assumptions: List[Assumption] = []
        flags: List[Flag] = []

        live_as_of = self.manifest.as_of("live")
        current = quarter_of(live_as_of)

        # periods ----------------------------------------------------------
        quarters: List[str] = []
        for period in raw.periods:
            resolved, problem, assumption = self._period(period, live_as_of, current)
            if resolved:
                quarters.append(resolved)
            if problem:
                problems.append(problem)
            if assumption:
                assumptions.append(assumption)
        quarters = list(dict.fromkeys(quarters))

        intent = raw.intent
        quarter = compare = compare_through = day = None

        if intent == "quarter_comparison":
            if live_as_of is None:
                problems.append(self._missing_as_of("live"))
            elif current != "Q2":
                problems.append(Problem(
                    "UNSUPPORTED_PERIOD",
                    "A same-point comparison needs the live extract to fall inside "
                    "Q2 2026; it is dated %s." % live_as_of.date()))
            else:
                quarter, compare = "Q2", "Q1"
                day = day_of_quarter("Q2", live_as_of)
                compare_through = same_elapsed_day(day, "Q1")
                basis = ("day %d of Q2 (%s) against day %d of Q1 (%s)"
                         % (day, live_as_of.date(), day, compare_through.date()))
                if "same point" in raw.phrases:
                    assumptions.append(Assumption(
                        "SAME_POINT",
                        "\"Same point\" means the same number of elapsed days from "
                        "the start of each quarter: %s. It does not mean the same "
                        "calendar date, business days, or pipeline stage." % basis))
                    flags.append(Flag(
                        "AMBIGUOUS_PHRASE", "warning",
                        "\"Same point\" has more than one reasonable meaning. It "
                        "was answered as the same elapsed day in each quarter (see "
                        "ASSUMPTIONS). If you meant something else, such as the same "
                        "calendar date last year, this comparison doesn't answer that."))
                else:
                    assumptions.append(Assumption(
                        "COMPARISON_BASIS",
                        "Q2 is incomplete, so it is compared with Q1 at the same "
                        "elapsed point rather than with Q1's final result: %s." % basis))
        elif intent is not None:
            if len(quarters) > 1:
                problems.append(Problem(
                    "MULTIPLE_PERIODS",
                    "That names more than one quarter (%s). Ask about one, or ask "
                    "how Q2 compares with Q1 at the same point." % " and ".join(quarters)))
            elif quarters:
                quarter = quarters[0]
            elif current:
                quarter = current
                if not any(a.code in ("THIS_QUARTER", "NOW") for a in assumptions):
                    assumptions.append(Assumption(
                        "NO_PERIOD",
                        "No period was named, so this answers for the current "
                        "quarter: %s %d, because the dataset is current as of %s."
                        % (current, YEAR, live_as_of.date())))
            elif live_as_of is None:
                problems.append(self._missing_as_of("live"))

        if quarter == "Q1" and intent in ("rep_risk", "coverage"):
            problems.append(Problem(
                "UNSUPPORTED_PERIOD",
                "At-risk and coverage only apply to a quarter still in progress. "
                "Q1 2026 is closed. Ask for one rep's Q1 attainment instead, e.g. "
                "\"What was Priya's Q1 attainment?\""))

        for q in filter(None, {quarter, compare}):
            if self.manifest.as_of(SNAPSHOT_FOR[q]) is None:
                problems.append(self._missing_as_of(SNAPSHOT_FOR[q]))

        # segment ----------------------------------------------------------
        segment = None
        resolved_segments = []
        for text in raw.segments:
            canon = SEGMENT_ALIASES.get(text.lower())
            if canon is None:
                problems.append(Problem(
                    "UNKNOWN_SEGMENT",
                    "\"%s\" isn't a segment in this data. The segments are "
                    "Enterprise, Mid-Market and SMB."
                    % _as_written(raw.question, text)))
                continue
            resolved_segments.append(canon)
            if text.lower() == "small business":
                assumptions.append(Assumption(
                    "SEGMENT_ALIAS", "\"Small business\" means the SMB segment."))
        resolved_segments = list(dict.fromkeys(resolved_segments))
        if len(resolved_segments) > 1:
            problems.append(Problem(
                "MULTIPLE_SEGMENTS",
                "That names more than one segment (%s). Ask about one, or leave "
                "the segment out to see every segment side by side."
                % " and ".join(resolved_segments)))
        elif resolved_segments:
            segment = resolved_segments[0]

        # rep --------------------------------------------------------------
        rep_id = rep_name = None
        found = []
        for mention in raw.people:
            rid, problem, assumption = self._rep(raw.question, mention)
            if problem:
                problems.append(problem)
            elif rid:
                found.append((rid, assumption))
        distinct = list(dict.fromkeys(r for r, _ in found))
        if len(distinct) > 1:
            problems.append(Problem(
                "MULTIPLE_REPS",
                "That names more than one rep (%s). Ask about one at a time, or "
                "ask which reps are at risk to see them all."
                % ", ".join(self.roster.name_of(r) for r in distinct)))
        elif distinct:
            rep_id = distinct[0]
            rep_name = self.roster.name_of(rep_id)
            for rid, assumption in found:
                if assumption and rid == rep_id:
                    assumptions.append(assumption)
                    break
            rep_segment = self.roster.segment_of(rep_id)
            if segment and rep_segment != segment:
                problems.append(Problem(
                    "REP_SEGMENT_CONFLICT",
                    "%s is in %s, not %s." % (rep_name, rep_segment, segment)))
            if intent == "quarter_comparison":
                problems.append(Problem(
                    "UNSUPPORTED_FILTER",
                    "Quarter comparisons are overall or by segment, not by rep."))

        # stages -----------------------------------------------------------
        stages = None
        if intent == "deal_list":
            if raw.stages is None:
                assumptions.append(Assumption(
                    "ALL_STAGES",
                    "No stage was named, so deals in every stage are listed: open, "
                    "Closed Won and Closed Lost."))
            elif raw.stages == "open":
                stages = OPEN_STAGES
            elif raw.stages == "won":
                stages = (WON,)
            elif raw.stages == "lost":
                stages = (LOST,)
            elif raw.stages in KNOWN_STAGES:
                stages = (raw.stages,)
            else:
                problems.append(Problem(
                    "UNKNOWN_STAGE", "\"%s\" isn't a stage in this data." % raw.stages))
            if quarter:
                start, end = QUARTERS[quarter]
                assumptions.append(Assumption(
                    "DEAL_LIST_SCOPE",
                    "\"Deals\" means every matching deal with a close date in %s %d "
                    "(%s to %s), read from the %s. Deals from other quarters are "
                    "not listed." % (quarter, YEAR, start.date(), end.date(),
                                     "extract taken at Q1 close" if quarter == "Q1"
                                     else "live extract")))

        # ambiguous phrases ------------------------------------------------
        if "on track" in raw.phrases and intent in ("rep_risk", "coverage"):
            assumptions.append(Assumption(
                "ON_TRACK",
                "\"On track\" is read as: closed-won plus open pipeline is at least "
                "quota, the same test used for \"at risk\". It is not a forecast, "
                "and it does not mean bookings are keeping pace with the calendar."))
            flags.append(Flag(
                "AMBIGUOUS_PHRASE", "warning",
                "\"On track\" has no single definition. It was answered with the "
                "at-risk test (see ASSUMPTIONS). A test of whether bookings are "
                "keeping pace with the calendar would give a different answer."))

        if intent is None and not problems:
            problems.append(Problem(
                "UNSUPPORTED_QUESTION",
                "I can't map that to something I know how to compute."))

        if problems:
            raise QueryRejected(_unique_problems(problems), raw)

        as_of = self.manifest.as_of(SNAPSHOT_FOR[quarter])
        _, end = QUARTERS[quarter]
        through = min(end, as_of)
        return ValidatedQuery(
            raw, intent, quarter, as_of, through, segment, rep_id, rep_name, stages,
            compare, compare_through, day, assumptions, flags)

    # ----------------------------------------------------------------------

    def _missing_as_of(self, snapshot_name: str) -> Problem:
        return Problem(
            "MISSING_AS_OF",
            "%s has no as-of date in snapshots.json. Without one, \"to date\", "
            "\"this quarter\" and whether a quarter is final can't be determined, "
            "so nothing is answered from it."
            % self.manifest.file(snapshot_name, "deals"))

    def _period(self, period: str, live_as_of, current) -> Tuple[
            Optional[str], Optional[Problem], Optional[Assumption]]:
        covers = "This data covers Q1 and Q2 2026 only."
        m = re.match(r"^Q([1-4])(?: (\d{4}))?$", period)
        if m:
            q, year = "Q" + m.group(1), m.group(2)
            if year and int(year) != YEAR:
                return None, Problem("UNSUPPORTED_PERIOD", "%s %s: %s" % (q, year, covers)), None
            if q not in QUARTERS:
                return None, Problem(
                    "UNSUPPORTED_PERIOD",
                    "%s %d: %s It holds no forecast." % (q, YEAR, covers)), None
            return q, None, None
        if period == "this quarter":
            if current is None:
                return None, None, None  # reported once as MISSING_AS_OF
            return current, None, Assumption(
                "THIS_QUARTER",
                "\"This quarter\" means %s %d, because the dataset is current as of %s."
                % (current, YEAR, live_as_of.date()))
        if period == "last quarter":
            if current is None:
                return None, None, None
            previous = PREVIOUS_QUARTER.get(current)
            if previous is None:
                return None, Problem("UNSUPPORTED_PERIOD", "The quarter before %s %d: %s"
                                     % (current, YEAR, covers)), None
            return previous, None, Assumption(
                "LAST_QUARTER",
                "\"Last quarter\" means %s %d, the quarter before %s %d, which the "
                "dataset is current in as of %s." % (previous, YEAR, current, YEAR,
                                                     live_as_of.date()))
        if period == "next quarter":
            return None, Problem(
                "UNSUPPORTED_PERIOD",
                "Next quarter hasn't started, and this data holds no forecast. %s" % covers), None
        if period.startswith(("month:", "grain:")):
            label = period.split(":", 1)[1]
            return None, Problem(
                "UNSUPPORTED_PERIOD",
                "\"%s\" isn't a quarter. Every figure here covers a whole quarter, or "
                "a quarter up to its snapshot date, so answering would silently "
                "change the time window." % label), None
        if period.startswith("year:"):
            return None, Problem(
                "UNSUPPORTED_PERIOD",
                "\"%s\" is a year, not a quarter. %s" % (period.split(":", 1)[1], covers)), None
        if period.startswith("as_of:"):
            text = period.split(":", 1)[1]
            asked = pd.to_datetime(text, errors="coerce")
            available = {name: self.manifest.as_of(name) for name in ("at_q1_close", "live")}
            if not pd.isna(asked):
                for name, date in available.items():
                    if date is not None and asked.normalize() == date:
                        return quarter_of(date), None, Assumption(
                            "AS_OF_REQUESTED",
                            "\"As of %s\" matches the %s snapshot date, %s."
                            % (text, "Q1 close" if name == "at_q1_close" else "live",
                               date.date()))
            dates = ", ".join("%s (%s)" % (d.date(), "Q1 close" if n == "at_q1_close" else "live")
                              for n, d in available.items() if d is not None)
            return None, Problem(
                "UNSUPPORTED_AS_OF",
                "There is no snapshot as of \"%s\". Data exists only as of %s, and "
                "figures for any other date would be invented." % (text, dates)), None
        if period.startswith("now:"):
            if current is None:
                return None, None, None
            word = period.split(":", 1)[1]
            return current, None, Assumption(
                "NOW",
                "\"%s\" means the live snapshot date, %s. No later data exists."
                % (word.capitalize(), live_as_of.date()))
        return None, Problem("UNSUPPORTED_PERIOD",
                             "\"%s\" isn't a period this tool recognises. %s"
                             % (period.split(":", 1)[-1], covers)), None

    def _rep(self, question: str, mention: str) -> Tuple[
            Optional[str], Optional[Problem], Optional[Assumption]]:
        reps = self.roster.valid()
        text = mention.strip().lower()
        written = _as_written(question, mention)

        if text in SEGMENT_ALIASES:
            return None, None, None

        m = re.match(r"^rep[\s-]?0*(\d+)$", text)
        if m:
            rid = "REP-%02d" % int(m.group(1))
            if rid in set(reps.rep_id):
                return rid, None, None
            return None, self._unknown_rep(written), None

        exact = reps[reps.rep_name.str.lower() == text]
        if len(exact) == 1:
            return exact.rep_id.iloc[0], None, None

        tokens = set(text.split())
        rep_hits = reps[[tokens <= set(n.lower().split()) for n in reps.rep_name]]
        managers = [mgr for mgr in reps.manager.dropna().unique()
                    if tokens <= set(mgr.lower().split())]
        options = (["%s (rep, %s)" % (r.rep_name, r.rep_id) for r in rep_hits.itertuples()]
                   + ["%s (manager)" % mgr for mgr in managers])

        if len(options) > 1:
            return None, Problem(
                "AMBIGUOUS_REP",
                "\"%s\" could mean %s. Use the full name."
                % (written, " or ".join(options))), None
        if len(rep_hits) == 1:
            row = rep_hits.iloc[0]
            return row.rep_id, None, Assumption(
                "PARTIAL_NAME",
                "\"%s\" means %s (%s), the only person on the roster with that name."
                % (written, row.rep_name, row.rep_id))
        if managers:
            manager = managers[0]
            seg = self.roster.manager_segment(manager)
            hint = (" In this data %s manages the entire %s segment, so ask about "
                    "%s for exactly that team." % (manager, seg, seg)) if seg else ""
            return None, Problem(
                "MANAGER_NOT_REP",
                "%s is a manager, not a rep, and results can't be filtered by "
                "manager.%s" % (manager, hint)), None
        return None, self._unknown_rep(written), None

    def _unknown_rep(self, written: str) -> Problem:
        names = ", ".join(self.roster.valid().rep_name)
        return Problem(
            "UNKNOWN_REP",
            "\"%s\" isn't a rep on the roster (%s) or a segment in this data. "
            "Reps: %s." % (written, self.roster.source, names))


def _as_written(question: str, text: str) -> str:
    m = re.search(re.escape(text), question, re.I)
    return m.group(0) if m else text


def _unique_problems(problems: List[Problem]) -> List[Problem]:
    seen, out = set(), []
    for p in problems:
        key = (p.code, p.message)
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out
