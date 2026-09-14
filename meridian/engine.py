"""The pipeline, in the order the guarantees depend on:

    1. parse     question -> RawQuery           (no data read)
    2. validate  RawQuery -> ValidatedQuery     (roster and manifest only)
    3. execute   ValidatedQuery -> Answer       (deal data loaded here, first time)

Deal files are loaded lazily, on the first question that passes validation, so
a question can't be shaped by what the data happens to contain.
"""
from __future__ import annotations

from typing import Optional

from . import metrics
from .data import ROOT, Manifest, Roster, SnapshotSet, load
from .model import Answer
from .query import default_parser
from .validate import ValidatedQuery, Validator


class Engine:
    def __init__(self, root: str = ROOT, parser=None, prefer_llm: bool = True):
        self.root = root
        self.manifest = Manifest.load(root)
        self.roster = Roster.load(root, self.manifest)
        self.parser = parser or default_parser(self.roster.name_phrases(), prefer_llm)
        self.validator = Validator(self.roster, self.manifest)
        self._snaps: Optional[SnapshotSet] = None

    @property
    def data_loaded(self) -> bool:
        return self._snaps is not None

    @property
    def snaps(self) -> SnapshotSet:
        if self._snaps is None:
            self._snaps = load(self.root, self.manifest)
        return self._snaps

    def interpret(self, question: str) -> ValidatedQuery:
        """Parse and validate. Raises QueryRejected. Reads no deal data."""
        return self.validator.validate(self.parser.parse(question))

    def ask(self, question: str) -> Answer:
        return metrics.execute(self.interpret(question), self.snaps)
