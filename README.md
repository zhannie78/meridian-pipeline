# Meridian Pipeline Intelligence

Ask plain-English pipeline questions. Every answer shows its source rows, formulas, assumptions and trust flags, so it can be recomputed by hand.

## Run

```bash
pip install -r requirements.txt
python3 app.py                       # http://127.0.0.1:8765
pip install pytest && python3 -m pytest -q   # 142 tests
```

No API key needed. `python3 ask.py "Why is Priya at risk?"` gives the same answers in the terminal.

## How it works

1. **Parse** (`query.py`): question → raw query, fields as written.
2. **Validate** (`validate.py`): check against the roster and `snapshots.json`, or reject with every problem listed. No deal data read yet.
3. **Select** (`selection.py`): rows chosen by readable filters; bad rows set aside, never counted.
4. **Compute** (`metrics.py`): figures, formulas, assumptions, flags.
5. **Render** (`serialize.py`): JSON for the page, which formats and computes nothing itself.

If `ANTHROPIC_API_KEY` is set, Claude fills step 1 only and never sees a deal value.

## Key finding: Q1 isn't final

The two extracts disagree on Q1 bookings: $6,041,000 at Q1 close vs $4,050,000 on 2026-05-02. The app's opening page reconciles the $1,991,000 to the dollar (recycled IDs, slipped close dates, reversals). Closed quarters are read from the close extract; Q2 from the latest.

## Core queries

- **Enterprise vs quota:** 4.5% booked, but 1.03x with open pipeline: covered with no room for slippage. Its only booking moved in from Q1; 2 of 4 reps are short.
- **Reps at risk:** 8 of 10 (won + open pipeline < quota). Largest gap: Priya Patel, $260,000.
- **Q2 vs same point in Q1:** 8.4% vs 21.3% of quota at day 32; 2.3% excluding deals moved from Q1.

## Assumptions

- "This quarter" = Q2 2026 (data as of 2026-05-02).
- "Same point" = same elapsed day of each quarter.
- "At risk" = closed-won + open pipeline below quota; pipeline not probability-weighted.
- Missing data is never treated as zero: figures become "at least" or "undefined".

## Deploy (Fly.io)

```bash
fly launch --no-deploy --copy-config
fly secrets set MERIDIAN_PASSWORD=<password>   # basic auth, any username
fly deploy
```

## Limits

- The rule parser covers tested phrasings; anything else is refused.
- 10 reps, 92 deals, two CSV extracts. Not tested at real scale.
