"""The browser app: real HTTP against the real engine, plus the recorded export."""
import json
import os
import threading
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from conftest import ROOT
import app as webapp


@pytest.fixture(scope="module")
def server():
    service = webapp.Service(ROOT)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), webapp.make_handler(service))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield "http://127.0.0.1:%d" % httpd.server_address[1]
    httpd.shutdown()
    httpd.server_close()


def get(base, path):
    with urllib.request.urlopen(base + path) as r:
        return r.status, r.headers.get("Content-Type"), r.read()


def post(base, question):
    req = urllib.request.Request(base + "/api/ask", data=json.dumps({"question": question}).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


ALL_SUGGESTED = [q for g in webapp.SUGGESTIONS for q in g["questions"]]


def test_page_is_served(server):
    status, ctype, body = get(server, "/")
    assert status == 200 and ctype.startswith("text/html")
    assert b"Meridian Pipeline Intelligence" in body


def test_meta(server):
    meta = json.loads(get(server, "/api/meta")[2])
    assert meta["as_of"] == "2026-05-02" and meta["mode"] == "live"
    assert meta["quarters"] == [
        {"label": "Q1 2026", "detail": "Jan 1\u2013Mar 31, closed"},
        {"label": "Q2 2026", "detail": "Apr 1\u2013Jun 30, day 32 of 91 (35%), as of 2026-05-02"}]


def test_bridge_endpoint(server):
    audit = json.loads(get(server, "/api/audit")[2])
    assert audit["kind"] == "audit" and audit["title"] == "The $1.99 M Bridge"
    assert audit["headline"] == "Q1 2026 bookings depend on which extract you open"
    assert [e["total"] for e in audit["extracts"]] == ["$6,041,000", "$4,050,000"]
    assert audit["difference"] == "$1,991,000" and audit["balanced"] is True


def test_waterfall_bars_chain_from_one_extract_to_the_other(server):
    chart = json.loads(get(server, "/api/audit")[2])["chart"]
    assert [b["label"] for b in chart] == ["$6.04M", "-$1,220K", "-$373K", "-$253K", "-$110K", "-$35K", "$4.05M"]
    assert chart[0]["end"] == 6_041_000 and chart[-1]["end"] == 4_050_000
    for prev, bar in zip(chart[1:-2], chart[2:-1]):
        assert bar["start"] == prev["end"]  # each step starts where the last one ended
    assert chart[-2]["end"] == chart[-1]["end"]


def test_bridge_rows_show_what_each_deal_was_and_is(server):
    lines = json.loads(get(server, "/api/audit")[2])["lines"]
    assert [l["amount"] for l in lines] == ["$6,041,000", "-$1,220,000", "-$373,000", "-$253,000",
                                           "-$110,000", "-$35,000", "$4,050,000"]
    assert [len(l["deals"]) for l in lines] == [51, 12, 3, 3, 1, 1, 32]
    opp076 = lines[1]["deals"][0]
    assert opp076["before"] == {"account": "Cascade Financial", "stage": "Closed Won",
                                "close": "2026-03-19", "value": "$112,000"}
    assert opp076["after"]["account"] == "Evergreen Capital"
    assert opp076["changed"] == ["account", "stage", "close", "value"]
    assert lines[5]["deals"][0]["changed"] == ["value"]  # OPP-021: only the value moved


def test_beyond_the_bridge(server):
    beyond = json.loads(get(server, "/api/audit")[2])["beyond"]
    assert [(b["severity"], b["title"]) for b in beyond] == [
        ("high", "Deal IDs recycled onto unrelated Q2 deals"),
        ("high", "Closed-Lost deals reopened or lost their reason"),
        ("ambiguity", "Deal region and rep territory disagree"),
        ("minor", "Deals past their own close date")]
    assert len(beyond[0]["deals"]) == 13
    assert [d["id"] for d in beyond[1]["deals"]] == ["OPP-008", "OPP-010", "OPP-032", "OPP-054"]
    assert len(beyond[2]["rows"]) == 17 and beyond[3]["ids"] == ["OPP-066", "OPP-075"]


def test_answer_carries_all_six_sections(server):
    p = post(server, "How is the Enterprise segment tracking against quota this quarter?")
    assert p["kind"] == "answer"
    for key in ("headline", "read_as", "metrics", "datasets", "method", "assumptions", "flags"):
        assert key in p
    A = next(d for d in p["datasets"] if d["key"] == "A")
    assert A["file"] == "Q2/q2_deals.csv" and A["ids"] == ["OPP-060"]
    assert A["rows"][0][0] == "8"  # CSV line number
    assert A["filters"][0] == 'stage = "Closed Won"'
    calc = p["method"]["calculations"][0]
    assert calc["steps"] == ["= $165,000 / $3,650,000", "= 4.52%"]
    assert p["method"]["query"]["segment"] == "Enterprise"
    assert p["flag_counts"] == {"critical": 1, "warning": 2, "context": 0}
    assert p["naive"]["differs"].startswith("It calls Enterprise well behind target")


def test_rejection(server):
    p = post(server, "Why is John Smith at risk in April?")
    assert p["kind"] == "rejection"
    assert {x["code"] for x in p["problems"]} == {"UNKNOWN_REP", "UNSUPPORTED_PERIOD"}


@pytest.mark.parametrize("question", ["", "x" * 501])
def test_empty_and_overlong_questions(server, question):
    assert post(server, question)["kind"] == "error"


def test_bad_json_is_a_400(server):
    req = urllib.request.Request(server + "/api/ask", data=b"{not json",
                                 headers={"Content-Type": "application/json"})
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(req)
    assert exc.value.code == 400


def test_every_suggested_question_serialises_without_nan():
    service = webapp.Service(ROOT)
    for q in ALL_SUGGESTED:
        payload = service.ask(q)
        text = json.dumps(payload, allow_nan=False)  # raises on NaN or infinity
        assert payload["kind"] in ("answer", "rejection"), q
        assert '"nan"' not in text.lower() and "NaT" not in text


def test_export_embeds_every_suggested_answer(tmp_path):
    out = tmp_path / "recorded.html"
    webapp.export(str(out), webapp.Service(ROOT))
    html = out.read_text()
    marker = "window.MERIDIAN_RECORDED = "
    start = html.index(marker) + len(marker)
    end = html.index(";</script>", start)
    recorded = json.loads(html[start:end].replace("<\\/", "</"))
    assert recorded["meta"]["mode"] == "recorded"
    assert set(recorded["answers"]) == set(ALL_SUGGESTED)
    assert recorded["audit"]["kind"] == "audit"
    assert "</script" not in html[start:end]  # data can't break out of its script tag


def test_naive_contrast_only_for_the_core_queries_and_enterprise_deals(server):
    for q in webapp.CORE_QUERIES:
        assert post(server, q)["naive"] is not None, q
    assert post(server, "how is the enterprise segment tracking against quota this quarter")["naive"] is not None
    assert post(server, "Show me the Enterprise deals.")["naive"] is not None
    for q in ("What's our pipeline coverage this quarter?", "Why is Priya at risk?", "Show me the Mid-Market deals."):
        assert post(server, q)["naive"] is None, q


def test_rep_chart_matches_the_answer_at_one_times_coverage(server):
    p = post(server, "Which reps are at risk of missing Q2?")
    c = p["rep_chart"]
    assert c["thresholds"][0] == "0.50x" and c["thresholds"][-1] == "1.30x"
    one = c["thresholds"].index("1.00x")
    assert c["default"] == one
    assert c["headings"][one] == "8 of 10 reps are below 1.00x coverage"
    aisha = next(r for r in c["reps"] if r["name"] == "Aisha Williams")
    assert (aisha["won"], aisha["open"], aisha["quota"], aisha["coverage"], aisha["reached"]) == (
        "$60,000", "$240,000", "$480,000", "0.63x", "12.5%")
    assert (aisha["won_n"], aisha["open_n"]) == ("1 deal", "3 deals")
    assert aisha["gaps"][one] == "$180,000" and aisha["status"][one] == "below"
    marcus = next(r for r in c["reps"] if r["name"] == "Marcus Rivera")
    assert marcus["gaps"][one] == "-$275,000" and marcus["status"][one] == "meets"
    assert marcus["status"][-1] == "meets" and c["headings"][-1] == "9 of 10 reps are below 1.30x coverage"
    assert post(server, "Why is Priya at risk?")["rep_chart"] is None


def test_rep_risk_explains_why_coverage_alone_decides(server):
    p = post(server, "Which reps are at risk of missing Q2?")
    text = next(a["text"] for a in p["assumptions"] if a["code"] == "AT_RISK_COVERAGE_ONLY")
    assert "shown, not scored" in text


def test_password_gate():
    import base64
    service = webapp.Service(ROOT)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), webapp.make_handler(service, "s3cret"))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = "http://127.0.0.1:%d" % httpd.server_address[1]
    try:
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(base + "/api/meta")
        assert exc.value.code == 401
        for pw, ok in (("wrong", False), ("s3cret", True)):
            req = urllib.request.Request(base + "/api/meta", headers={
                "Authorization": "Basic " + base64.b64encode(("anyone:" + pw).encode()).decode()})
            if ok:
                assert urllib.request.urlopen(req).status == 200
            else:
                with pytest.raises(urllib.error.HTTPError):
                    urllib.request.urlopen(req)
    finally:
        httpd.shutdown()
        httpd.server_close()
