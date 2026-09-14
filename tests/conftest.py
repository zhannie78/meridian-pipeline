"""Shared fixtures.

Edge-case tests never hand-build DataFrames. They copy the real CSVs into a
temporary folder, change specific cells, and run the whole pipeline on that, so
they exercise the same file reading and row checks the real data goes through.
"""
import json
import os
import sys

import pandas as pd
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from meridian.engine import Engine  # noqa: E402

FILES = ("Q1/q1_deals.csv", "Q1/q1_reps.csv", "Q2/q2_deals.csv", "Q2/q2_reps.csv")


@pytest.fixture(scope="session")
def engine():
    return Engine(prefer_llm=False)


def build_dataset(folder, edits=None, manifest=None, drop=()):
    """Copy the real data into `folder`.

    edits:    {relative path: function(raw string DataFrame) -> DataFrame}
    manifest: function(manifest dict) that edits snapshots.json in place
    drop:     relative paths to leave out entirely
    """
    edits = edits or {}
    for rel in FILES:
        if rel in drop:
            continue
        df = pd.read_csv(os.path.join(ROOT, rel), dtype=str, keep_default_na=False)
        if rel in edits:
            df = edits[rel](df)
        dst = os.path.join(str(folder), rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        df.to_csv(dst, index=False)
    with open(os.path.join(ROOT, "snapshots.json")) as fh:
        doc = json.load(fh)
    if manifest:
        manifest(doc)
    with open(os.path.join(str(folder), "snapshots.json"), "w") as fh:
        json.dump(doc, fh)
    return str(folder)


def set_cell(key_value, column, value, key="deal_id"):
    def edit(df):
        df = df.copy()
        assert (df[key] == key_value).any(), "%s not found" % key_value
        df.loc[df[key] == key_value, column] = value
        return df
    return edit


def chain(*edits):
    def edit(df):
        for e in edits:
            df = e(df)
        return df
    return edit


@pytest.fixture
def make_engine(tmp_path):
    def _make(edits=None, manifest=None, drop=()):
        return Engine(build_dataset(tmp_path, edits, manifest, drop), prefer_llm=False)
    return _make
