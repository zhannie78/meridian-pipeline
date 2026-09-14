"""Parsing: English in, a raw query object out.

The parser records what the question asks for, as written: an intent, and any
segment, rep, period, stage or ambiguous phrase it mentions. It resolves
nothing. Matching "Priya" to REP-03, or "this quarter" to Q2 2026, is
validate.py's job, and it does that against the roster and the snapshot
manifest only. No deal row is read until a query has passed validation.

Two parsers produce the same RawQuery:

    RuleParser  deterministic pattern matching. No network, no key. The demo
                runs on this.
    LLMParser   Claude, constrained to JSON with the same fields. Enable with
                ANTHROPIC_API_KEY.

Neither is trusted. The model can only fill in fields that the validator then
checks, and it is never shown a deal value.

Some questions are refused here, before validation, because no metric could
answer them however they were resolved: forecasts, region questions (region is
ambiguous in this data), measures that aren't computed, and pronouns that point
at an earlier question.
"""
from __future__ import annotations

import json
import os
import re
from typing import Iterable, List, Optional

INTENTS = ("attainment", "rep_risk", "quarter_comparison", "coverage",
           "deal_list", "audit")

CAPABILITIES = (
    "I can answer: segment or rep attainment against quota, which reps are at "
    "risk and why, Q2 against the same point in Q1, pipeline coverage, and "
    "deal lists behind any of those, overall, by segment or by rep.")


class Problem:
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message

    def __repr__(self):
        return "<Problem %s>" % self.code


class QueryRejected(Exception):
    """The question can't be answered as asked. Carries every problem found."""

    def __init__(self, problems: List[Problem], raw=None):
        self.problems = problems
        self.raw = raw
        super().__init__("; ".join("%s: %s" % (p.code, p.message) for p in problems))

    @property
    def codes(self) -> List[str]:
        return [p.code for p in self.problems]


class RawQuery:
    """What the question says, before anything is resolved or checked."""

    FIELDS = ("intent", "segments", "people", "periods", "stages", "phrases")

    def __init__(self, question: str, intent: Optional[str] = None,
                 segments: Optional[List[str]] = None,
                 people: Optional[List[str]] = None,
                 periods: Optional[List[str]] = None,
                 stages: Optional[str] = None,
                 phrases: Optional[List[str]] = None,
                 problems: Optional[List[Problem]] = None,
                 source: str = "rules"):
        self.question = question
        self.intent = intent
        self.segments = segments or []
        self.people = people or []
        self.periods = periods or []
        self.stages = stages
        self.phrases = phrases or []
        self.problems = problems or []
        self.source = source

    def as_dict(self) -> dict:
        return {f: getattr(self, f) for f in self.FIELDS}


# ---------------------------------------------------------------------------
# questions no metric can answer
# ---------------------------------------------------------------------------

_FORECAST = re.compile(
    r"\bwill we\b|\bgoing to\b|\bforecast|\bpredict|\bprojected\b|"
    r"\bprojection\b|\bexpect(ed)? to (close|hit|land|finish|make)\b|"
    r"\blikely to\b|\bgonna\b")

_REGION = re.compile(
    r"\bregions?\b|\bwest\b|\beast\b|\bnortheast\b|\bsoutheast\b|"
    r"\bcentral\b|\bterritor(y|ies)\b")

_PRONOUN = re.compile(
    r"\b(she|he|her|him|his|hers|they|them)\b|\btheir (deals|pipeline)\b|"
    r"\b(this|that) (rep|person|segment|team|deal)\b|\b(these|those) (deals|reps)\b")

_UNSUPPORTED_MEASURES = [
    (r"\baverage\b|\bavg\b|\bmedian\b|\bdeal sizes?\b", "averages or deal size"),
    (r"\bwin[\s-]?rates?\b|\bconversion\b|\bclose rates?\b", "win rate"),
    (r"\bcycles?\b|\bvelocity\b|\bdays to close\b", "sales cycle length"),
    (r"\bcreated?\b|\bcreation\b|\bgenerated?\b|\bnew pipeline\b", "pipeline creation"),
    (r"\blos[et]\b.*\bto\b|\bwhy (did )?we los|\bloss reasons?\b|"
     r"\bcompetitors?\b|\bsalesforce\b|\bsap\b", "loss analysis"),
    (r"\bproducts?\b|\bproduct lines?\b", "product lines"),
    (r"\bstalled\b|\brisky deals\b|\bkey deals\b|\bnew deals\b",
     "deal characteristics this data doesn't record"),
]


def text_guard(q: str) -> List[Problem]:
    problems = []
    if _FORECAST.search(q):
        problems.append(Problem(
            "FORECAST",
            "That asks for a forecast. This data has no trustworthy win rate "
            "(reversed wins and recycled deal IDs destroyed the conversion "
            "history), so any projected number would be invented. For where "
            "things stand today, ask about coverage or risk."))
    if _REGION.search(q):
        problems.append(Problem(
            "REGION_AMBIGUOUS",
            "Region is ambiguous in this dataset: 17 deals sit in a different "
            "region than their rep's territory. Ask by segment or rep instead, "
            "or run `python ask.py audit` to see the conflict."))
    m = _PRONOUN.search(q)
    if m:
        problems.append(Problem(
            "AMBIGUOUS_REFERENCE",
            "\"%s\" points at something from an earlier question. Each question "
            "is answered on its own, with no memory of the last one, so name "
            "the rep or segment." % m.group(0)))
    for pattern, measure in _UNSUPPORTED_MEASURES:
        if re.search(pattern, q):
            problems.append(Problem(
                "UNSUPPORTED_MEASURE",
                "That asks about %s, which isn't something this tool computes. "
                "It won't substitute a metric that sounds close." % measure))
            break
    return problems


# ---------------------------------------------------------------------------
# mentions
# ---------------------------------------------------------------------------

_MONTHS = ("january|february|march|april|june|july|august|september|"
           "october|november|december")

# Applied in order; each match is blanked out so later patterns can't reuse it.
_PERIOD_PATTERNS = [
    (r"\bas of ([a-z0-9][a-z0-9 ,/-]*?)(?=\?|$|\.|,| for\b| in\b| and\b)",
     lambda m: "as_of:" + m.group(1).strip()),
    (r"\bq([1-4])\s*(?:fy\s*)?'?(20\d\d|\d\d)\b",
     lambda m: "Q%s %s" % (m.group(1), _year(m.group(2)))),
    (r"\bq([1-4])\b", lambda m: "Q" + m.group(1)),
    (r"\b(first|second|third|fourth) quarter\b",
     lambda m: "Q%d" % ("first second third fourth".split().index(m.group(1)) + 1)),
    (r"\b(this|current) quarter\b|\bquarter[\s-]to[\s-]date\b|\bqtd\b",
     lambda m: "this quarter"),
    (r"\b(last|previous|prior) quarter\b", lambda m: "last quarter"),
    (r"\bnext quarter\b", lambda m: "next quarter"),
    (r"\b(%s)\b" % _MONTHS, lambda m: "month:" + m.group(1)),
    (r"\b(?:in|during|for|since|through|by|of|from|until) (may)\b",
     lambda m: "month:may"),
    (r"\b(this|last|next) (month|week|year)\b", lambda m: "grain:" + m.group(0)),
    (r"\b(monthly|weekly|daily|annual|annually|ytd|year[\s-]to[\s-]date|h1|h2|"
     r"fy\d{0,4}|fiscal year)\b", lambda m: "grain:" + m.group(0)),
    (r"\b(20\d\d)\b", lambda m: "year:" + m.group(1)),
    (r"\b(today|right now|so far|to date)\b", lambda m: "now:" + m.group(1)),
]


def _year(text: str) -> str:
    return text if len(text) == 4 else "20" + text


_SEGMENT_ALIASES = [
    (r"\bmid[\s-]?market\b|\bmidmarket\b", "mid-market"),
    (r"\bsmall business(es)?\b", "small business"),
    (r"\bsmb\b", "smb"),
    (r"\benterprise\b", "enterprise"),
]
_UNKNOWN_SEGMENT_WORDS = re.compile(
    r"\b(commercial|strategic|public sector|federal|government|corporate|"
    r"emerging|startups?|sme|mid[\s-]?size|large enterprise|key accounts?|"
    r"named accounts?|global accounts?)\b")
_SEGMENT_SLOT = re.compile(r"\b([a-z][a-z-]*(?: [a-z][a-z-]*)?) segment\b")
_SLOT_STOPWORDS = {
    "the", "each", "every", "which", "what", "a", "an", "per", "by", "our",
    "this", "that", "is", "how", "of", "in", "for", "and", "its", "any", "all",
    "one", "same", "my", "whole", "entire", "doing", "tracking", "vs", "versus",
    "compare", "at", "to", "show", "me", "deals", "rep", "reps", "are"}

_NON_PERSON = {
    "we", "us", "our", "team", "org", "organisation", "organization", "company",
    "business", "meridian", "everyone", "everybody", "sales", "reps", "rep", "it",
    "q1", "q2", "q3", "q4", "quarter", "pipeline", "segment", "segments",
    "enterprise", "mid-market", "midmarket", "mid", "market", "smb", "deals",
    "deal", "things", "overall", "open", "closed", "won", "lost", "closed-won",
    "closed-lost", "active", "prospecting", "discovery", "proposal",
    "negotiation", "current", "all", "remaining", "pending", "biggest",
    "largest", "top", "quota", "bookings", "numbers", "this", "last", "the",
    "small", "total", "who", "which", "anyone", "somebody"}

_PERSON_SLOTS = [
    r"\bwhy (?:is|are|has|have) (?P<x>[a-z][a-z .'-]*?) (?:at risk|behind|"
    r"struggling|missing|short|off track|not on track|below|under)\b",
    r"\bis (?P<x>[a-z][a-z .'-]*?) (?:at risk|behind|on track|off track|struggling|short)\b",
    r"\bhow (?:is|are|has|have) (?P<x>[a-z][a-z .'-]*?) (?:doing|tracking|performing|looking|going)\b",
    r"\b(?P<x>[a-z][a-z-]*(?: [a-z][a-z-]*)?)'s (?:deals?|pipeline|quota|numbers?|"
    r"bookings|attainment|coverage|team|territory|accounts?|risk)\b",
    r"\b(?:deals?|pipeline|quota|bookings|attainment|coverage) (?:for|of|owned by|"
    r"belonging to) (?P<x>[a-z][a-z .'-]*?)(?=\?|$|\.|,| this\b| last\b| in\b| "
    r"for\b| q[1-4]\b| against\b)",
    r"\b(?:show|list|see|pull|give|display)(?: me)? (?:the |all |our )?"
    r"(?P<x>[a-z][a-z .'-]*?) deals\b",
    r"\brep (?P<x>[a-z][a-z-]*(?: [a-z][a-z-]*)?)",
]

_ALLOWED_CAPS = {
    "i", "enterprise", "mid-market", "midmarket", "mid", "market", "smb",
    "closed", "won", "lost", "closed-won", "closed-lost", "open", "prospecting",
    "discovery", "proposal", "negotiation", "pipeline", "meridian", "quota",
    "crm", "cco", "ceo", "cro", "vp", "segment", "reps", "rep", "deals", "deal",
    "me", "the", "all", "our", "my", "what", "which", "who", "why", "how",
    "show", "list", "is", "are", "and", "of", "for", "in", "at", "q", "ok",
    "please", "coverage", "attainment", "risk"}
_ALLOWED_CAPS.update(_MONTHS.split("|"))
_ALLOWED_CAPS.update({"may", "monday", "tuesday", "wednesday", "thursday", "friday"})


def _proper_nouns(question: str) -> List[str]:
    """Capitalised words mid-sentence that aren't known terms: likely names."""
    out = []
    for sentence in re.split(r"(?<=[.?!])\s+", question.strip()):
        tokens = re.findall(r"[A-Za-z][A-Za-z'-]*", sentence)
        run = []
        for idx, token in enumerate(tokens):
            word = re.sub(r"'s$", "", token)
            capital = word[:1].isupper() and not word.isupper()
            if idx == 0 or not capital or word.lower() in _ALLOWED_CAPS:
                if run:
                    out.append(" ".join(run))
                run = []
                continue
            run.append(word)
        if run:
            out.append(" ".join(run))
    return out


def _clean_slot(text: str) -> str:
    words = [w for w in re.split(r"\s+", text.strip(" .'-")) if w]
    while words and words[0] in ("the", "our", "a", "an", "rep"):
        words = words[1:]
    return " ".join(words)


# ---------------------------------------------------------------------------
# intents
# ---------------------------------------------------------------------------

_SUBJECT = (r"\b(we|us|our|the (org|team|company|business)|enterprise|"
            r"mid[\s-]?market|smb|q1|q2|this quarter|last quarter)\b")
_VAGUE = r"\b(tracking|doing|do|performing|looking|going)\b"

# Order matters: the first match wins, so specific intents come before
# attainment. There are no catch-alls like "how is" or "sales".
_INTENT_PATTERNS = [
    ("audit", r"\baudit\b|\breconcil|\btrust\b|\bdiscrepan|\bdisagree|"
              r"\bdata quality\b|\bwhy .*(differ|don'?t match|not match)"),
    ("deal_list", r"\bdeals?\b.*\bmakes? up\b|\bmakes? up\b|"
                  r"\b(show|list|see|pull|give|display)\b.*\bdeals?\b|"
                  r"\b(which|what) deals\b|\bhow many deals\b|"
                  r"\bwhat(?:'s| is) in the pipeline\b"),
    ("quarter_comparison", r"\bcompared?\b|\bcomparison\b|\bsame (point|time)\b|"
                           r"\bversus\b|\bvs\.?\b|\bpace\b|\bq1\b.*\bq2\b|"
                           r"\bq2\b.*\bq1\b|\bwhere we were\b|"
                           r"\b(ahead of|behind) last quarter\b"),
    ("rep_risk", r"\bat risk\b|\brisk\b|\bmiss(ing|es|ed)?\b|\bbehind\b|"
                 r"\bshortfall\b|\bstruggling\b|\bhow many reps\b|\boff track\b"),
    ("coverage", r"\bcoverage\b|\bcovered\b|\benough pipeline\b|"
                 r"\b(the|our|total|open|q1|q2) pipeline\b|"
                 r"\bpipeline (for|in|this|against|vs|health)\b"),
    ("attainment", r"\battainment\b|\bquota\b|\bbook(ed|ing|ings)?\b|"
                   r"\bclosed[\s-]won\b|" + _SUBJECT + r".*" + _VAGUE),
]

_STAGE_PATTERNS = [
    ("won", r"\bclosed[\s-]won\b|\bwon\b|\bwins\b|\bbooked\b|\b(we|did we) closed?\b"),
    ("lost", r"\bclosed[\s-]lost\b|\blost\b|\blosses\b"),
    ("open", r"\bopen\b|\bpipeline\b|\bactive\b|\bin[\s-]flight\b"),
    ("Prospecting", r"\bprospecting\b"),
    ("Discovery", r"\bdiscovery\b"),
    ("Proposal", r"\bproposals?\b"),
    ("Negotiation", r"\bnegotiation\b"),
]


class RuleParser:
    """Pattern matching. Boring on purpose: it can't invent a metric."""

    name = "rules"

    def __init__(self, known_names: Iterable[str] = ()):
        # Roster names let the parser spot "Priya" anywhere in a question.
        # Whether "Priya" is a real, unambiguous rep is still the validator's call.
        self.known_names = sorted({n.lower() for n in known_names}, key=len, reverse=True)

    def parse(self, question: str) -> RawQuery:
        q = question.lower().strip()
        raw = RawQuery(question, source=self.name)
        if not q:
            raw.problems.append(Problem("EMPTY_QUESTION", "The question is empty."))
            return raw

        raw.problems.extend(text_guard(q))
        raw.periods = self._periods(q)
        raw.segments = self._segments(q)
        raw.people = self._people(question, q, raw.segments)
        for phrase, pattern in (("on track", r"\b(on|off) track\b"),
                                ("same point", r"\bsame (point|time)\b")):
            if re.search(pattern, q):
                raw.phrases.append(phrase)
        raw.intent = self._intent(q, raw)
        if raw.intent == "deal_list":
            raw.stages = self._stages(q)
        if raw.intent is None and not raw.problems:
            raw.problems.append(Problem(
                "UNSUPPORTED_QUESTION",
                "I can't map that to something I know how to compute."))
        return raw

    def _intent(self, q: str, raw: RawQuery) -> Optional[str]:
        if "on track" in raw.phrases and not re.search(r"\bdeals?\b", q):
            return "rep_risk" if (raw.people or re.search(r"\breps?\b", q)) else "coverage"
        for intent, pattern in _INTENT_PATTERNS:
            if re.search(pattern, q):
                return intent
        if raw.people and re.search(_VAGUE, q):
            return "rep_risk"
        return None

    def _periods(self, q: str) -> List[str]:
        text, found = q, []
        for pattern, render in _PERIOD_PATTERNS:
            for m in list(re.finditer(pattern, text)):
                found.append(render(m))
            text = re.sub(pattern, lambda m: " " * len(m.group(0)), text)
        return found

    def _segments(self, q: str) -> List[str]:
        found = []
        text = q
        for m in _UNKNOWN_SEGMENT_WORDS.finditer(text):
            found.append(m.group(1))
        text = _UNKNOWN_SEGMENT_WORDS.sub(" ", text)
        for m in _SEGMENT_SLOT.finditer(text):
            words = [w for w in m.group(1).split() if w not in _SLOT_STOPWORDS]
            candidate = " ".join(words)
            if candidate and not any(re.search(p, candidate) for p, _ in _SEGMENT_ALIASES):
                found.append(candidate)
        for pattern, alias in _SEGMENT_ALIASES:
            if re.search(pattern, text):
                found.append(alias)
                text = re.sub(pattern, " ", text)
        return _unique(found)

    def _people(self, question: str, q: str, segments: List[str]) -> List[str]:
        found = []
        claimed = q
        for name in self.known_names:
            pattern = r"\b" + re.escape(name) + r"\b"
            if re.search(pattern, claimed):
                found.append(name)
                claimed = re.sub(pattern, " ", claimed)

        for pattern in _PERSON_SLOTS:
            for m in re.finditer(pattern, q):
                slot = _clean_slot(m.group("x"))
                words = set(slot.replace("'s", "").split())
                if not slot or words & _NON_PERSON or _PRONOUN.search(slot):
                    continue
                if any(slot == s or slot in s or s in slot for s in segments):
                    continue
                if any(slot in name or name in slot for name in found):
                    continue
                found.append(slot)

        for noun in _proper_nouns(question):
            low = noun.lower()
            if any(low in name or name in low for name in found):
                continue
            if any(low == s or s in low for s in segments):
                continue
            found.append(low)
        return _unique(found)

    def _stages(self, q: str) -> Optional[str]:
        for stage, pattern in _STAGE_PATTERNS:
            if re.search(pattern, q):
                return stage
        return None


def _unique(items: List[str]) -> List[str]:
    seen, out = set(), []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


# ---------------------------------------------------------------------------
# model parser
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You turn a sales leader's question into a query object for \
a reporting tool. You do not answer the question. Never state a number, name a \
deal, or estimate a figure: downstream code computes everything from the CRM.

Copy names, segments and periods exactly as the question writes them. Do not \
correct, expand or resolve them. "Priya" stays "Priya"; "this quarter" stays \
"this quarter". Validation happens after you.

Reply with one JSON object and nothing else:

  {"intent": <"attainment" | "rep_risk" | "quarter_comparison" | "coverage" | "deal_list" | "audit" | null>,
   "segments": [<segment names as written>],
   "people": [<person names as written>],
   "periods": [<periods as written, e.g. "this quarter", "Q1", "april">],
   "stages": <"open" | "won" | "lost" | a stage name | null>,
   "phrases": [<"on track" and/or "same point" if the question uses them>]}

Intent guide:
  attainment          bookings against quota
  rep_risk            which reps are at risk, or why one rep is
  quarter_comparison  this quarter against the same point in a prior quarter
  coverage            open pipeline plus bookings against quota
  deal_list           list the deals behind something
  audit               data quality, or why two reports disagree
Use null if none fits."""


class LLMParser:
    """Claude, limited to filling in RawQuery fields as written."""

    name = "claude"

    def __init__(self, model: str = "claude-sonnet-5", api_key: Optional[str] = None):
        try:
            import anthropic
        except ImportError:
            raise QueryRejected([Problem(
                "PARSER_UNAVAILABLE", "anthropic SDK not installed (pip install anthropic)")])
        key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise QueryRejected([Problem("PARSER_UNAVAILABLE", "ANTHROPIC_API_KEY is not set")])
        self._client = anthropic.Anthropic(api_key=key)
        self._model = model

    def parse(self, question: str) -> RawQuery:
        guard = text_guard(question.lower().strip())

        resp = self._client.messages.create(
            model=self._model, max_tokens=400, system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": question}])
        text = "".join(b.text for b in resp.content if b.type == "text").strip()
        match = re.search(r"\{.*\}", text, re.S)
        if not match:
            raise QueryRejected([Problem(
                "PARSER_OUTPUT", "model did not return JSON; got: %s" % text[:200])])
        try:
            payload = json.loads(match.group(0))
        except ValueError as exc:
            raise QueryRejected([Problem("PARSER_OUTPUT", "model returned malformed JSON: %s" % exc)])

        invented = set(payload) - set(RawQuery.FIELDS)
        if invented:
            raise QueryRejected([Problem(
                "PARSER_OUTPUT", "model invented fields: %s" % ", ".join(sorted(invented)))])
        intent = payload.get("intent")
        if intent is not None and intent not in INTENTS:
            raise QueryRejected([Problem("PARSER_OUTPUT", "model invented intent %r" % intent)])
        for field in ("segments", "people", "periods", "phrases"):
            value = payload.get(field, [])
            if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
                raise QueryRejected([Problem(
                    "PARSER_OUTPUT", "%s must be a list of strings" % field)])

        raw = RawQuery(
            question, intent=intent,
            segments=[s.lower() for s in payload.get("segments", [])],
            people=[p.lower() for p in payload.get("people", [])],
            periods=[_normalise_period(p) for p in payload.get("periods", [])],
            stages=payload.get("stages"),
            phrases=[p.lower() for p in payload.get("phrases", [])],
            problems=guard, source=self.name)
        if raw.intent is None and not raw.problems:
            raw.problems.append(Problem(
                "UNSUPPORTED_QUESTION", "I can't map that to something I know how to compute."))
        return raw


def _normalise_period(text: str) -> str:
    """Run a model-supplied period through the same patterns as the rule parser."""
    periods = RuleParser()._periods(text.lower())
    return periods[0] if periods else "unrecognised:" + text.lower()


def default_parser(known_names: Iterable[str] = (), prefer_llm: bool = True):
    if prefer_llm and os.environ.get("ANTHROPIC_API_KEY"):
        try:
            return LLMParser()
        except QueryRejected:
            pass
    return RuleParser(known_names)
