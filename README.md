# Meridian Pipeline Intelligence

Ask a plain-English question about the sales pipeline and get a direct answer, the exact data behind it, and a clear warning wherever the data is incomplete or an assumption was made.

> **Easier to read:** open **`README.html`** in this folder (double-click it). It has the same content, formatted, with copy buttons for each command.

**Contents:** [Hosted app](#use-the-hosted-app) · [Run it on your computer](#run-it-on-your-own-computer) · [5-minute tour](#a-5-minute-tour) · [How the design earns trust](#how-the-design-earns-trust) · [What it found](#what-it-found) · [Glossary](#glossary) · [How it's built](#how-its-built-technical)

---

## Use the hosted app

> ### ▶ Open it in your browser, nothing to install
>
> **App link:** https://meridian-pipeline.fly.dev/
>
> **Password:** **meridian-rpGX27WYLetp** (any username works)
>
> The first load can take a few seconds while the server wakes up.

---

## Run it on your own computer

*Optional, and no technical background needed.* This runs the same app locally, from the downloaded zip. You start it once from a terminal window, and then everything happens in your browser. It takes about five minutes the first time, needs no API key, and needs no internet connection once installed.

#### 1. Open a terminal window

- **Mac:** press `⌘ Command` + `Space`, type **Terminal**, press Return.
- **Windows:** click Start, type **PowerShell**, press Enter.

A terminal is a window where you type commands instead of clicking. You'll paste a few lines, one at a time, pressing Enter after each.

#### 2. Check that Python is installed

| Mac | Windows |
|---|---|
| `python3 --version` | `py --version` |

You should see something like `Python 3.9.6`. Any version 3.9 or higher is fine. If you see "command not found" or an older version, install Python from https://www.python.org/downloads/ (on Windows, tick **"Add python.exe to PATH"**), then open a new terminal.

#### 3. Go to the project folder

Unzip the download; the folder is usually called `meridian-pipeline-main`. In the terminal, type `cd ` (with a space), **drag the folder into the terminal window**, and press Enter. For example:

```bash
cd ~/Downloads/meridian-pipeline-main
```

#### 4. Set up the app (first time only)

Paste these one at a time. The last line takes about a minute and prints a lot of text; that's normal.

**Mac:**

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

**Windows (PowerShell):**

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

This makes a private space for the app inside its own folder (you'll see `(.venv)` at the start of the line), so nothing is installed elsewhere on your computer, then downloads the one data library it needs.

#### 5. Start the app

```bash
python app.py
```

Your browser opens to the app at **http://127.0.0.1:8765**. If it doesn't, copy that address into your browser. **Keep the terminal window open** while you use it; press **Ctrl+C** in the terminal to stop.

**Opening it again later:** go to the folder (step 3), run `source .venv/bin/activate` (Mac) or `.venv\Scripts\Activate.ps1` (Windows), then `python app.py`.

#### If something goes wrong

| What you see | What to do |
|---|---|
| `command not found` for `python3` or `py` | Install Python (step 2), then open a **new** terminal. |
| Windows: "running scripts is disabled" | Run `Set-ExecutionPolicy -Scope Process Bypass`, then repeat the `Activate.ps1` line. |
| `No such file or directory: requirements.txt` | The terminal isn't in the project folder. Repeat step 3. |
| `Address already in use` | Run `python app.py --port 9000` and open http://127.0.0.1:9000. |
| The browser didn't open | Copy http://127.0.0.1:8765 into your browser. |
| `pip install` shows an error | Run `python -m pip install --upgrade pip`, then repeat the install. |

The app needs no API key or account. Running locally, only your own computer can reach it.

---

## A 5-minute tour

Suggested questions are on the left of the app, grouped as *Core queries*, *Follow-up examples* and *Try to break it*.

| Do this | Notice this |
|---|---|
| Ask *"How is the Enterprise segment tracking against quota this quarter?"* | **Read as** shows how the question was understood before any number. The answer pairs bookings with open pipeline, because mid-quarter bookings alone always look alarming. Click **[A]** under Source data to see the exact deal row and its line number. |
| Open **Trust Flags**, then **Assumptions** | Flags explain what could mislead you about this answer (its only booking was really won last quarter). Assumptions list every definition used. |
| Ask *"Which reps are at risk of missing Q2?"* | Drag the coverage threshold slider and sort by dollar gap. At 1.00x the chart matches the answer; other positions are labelled what-ifs. |
| Ask *"How does Q2 attainment compare to where we were at the same point in Q1?"* | "Same point" is ambiguous, so the answer states how it was read and checks the other reading gives the same result. |
| Click **Show what a naive tool would say** | On the core questions: what a quick AI tool would say from the same file, and what it gets wrong. |
| Try *"Why is John Smith at risk?"* and *"How is Lisa tracking against quota?"* | No John Smith exists, so it refuses and lists real reps. "Lisa" is both a rep and a manager, so it asks which. |
| Click **The $1.99 M Bridge** | The two data extracts disagree about last quarter by $1.99M; every dollar is traced to specific deals. |

**Every answer has the same six sections:**
1. Direct answer
2. Supporting metrics
3. Source data
4. Method (including calculations)
5. Assumptions
6. Trust Flags

---

## How the design earns trust

Meridian's previous AI reporting tool produced wrong numbers and mixed up segments, and lost leadership's confidence. The likely cause: it let an AI model produce figures directly. Language models give the most plausible-sounding answer, and a plausible wrong number looks exactly like a right one. This prototype is designed so that can't happen, and so anyone can check it.

**Same numbers, different conclusions:**
- *A quick AI tool:* "Enterprise has closed $165,000 against a $3,650,000 Q2 quota: 4.5% attainment. Enterprise is well behind target."
- *This tool:* "Enterprise has booked 4.5% of its Q2 quota, but with open pipeline it's at 1.03x: technically covered, with no room for slippage."

| Design choice | What it means | Where you'll see it |
|---|---|---|
| **1. The AI never produces a number** | Every figure is calculated by ordinary code from the deal records. At most, an AI model helps turn the question into a structured request, and every field is checked. The version you run uses no AI model at all. | Every answer |
| **2. Every answer shows its evidence** | File, data date, exact filters, every deal row with its line number, and the formula with its inputs. | Source data, Method; click [A] or a deal ID |
| **3. It shows how it understood you** | The question is restated as the tool read it, before any number. | "Read as" line |
| **4. Definitions are printed, not assumed** | What "this quarter", "at risk" and "same point" mean, and which extract was used. | Assumptions |
| **5. It refuses rather than guesses** | Unknown people, ambiguous names, unsupported periods and forecasts are refused, with how to rephrase. | "Try to break it" questions |
| **6. It warns you about the data itself** | Deals moved between quarters, reused deal IDs, stale close dates, reps short inside a healthy total. | Trust Flags (open by default) |
| **7. Missing data never becomes zero** | Blank values make figures "at least"; missing quota makes ratios "undefined"; too-close verdicts say "unknown". | Answers on incomplete data |
| **8. It is tested against known answers** | 142 automated checks, including the core questions pinned to independently worked-out figures, and runs on deliberately damaged data. | `tests/` |

**Where it still falls short**
- Correct maths on flawed data is still flawed. The tool makes data problems visible; it doesn't fix the CRM.
- The definitions are the builder's, not yet Meridian's. They need sign-off from sales operations.
- It understands a limited set of phrasings and hasn't been tested on how Meridian's leaders actually ask.
- Evidence only helps if someone looks at it.
- It's a prototype on sample data (10 reps, 92 deals, two extracts), with no live CRM connection or scale testing.

---

## What it found

**Q1 isn't final.** Q1 bookings are $6,041,000 (51 deals, 102.0% of quota) in the extract taken at Q1 close, but $4,050,000 (32 deals, 68.4%) in the May 2 extract.

| Why the totals differ | Deals | Amount |
|---|---:|---:|
| Deal IDs reused for different deals | 12 | −$1,220,000 |
| Close date moved into Q2 | 3 | −$373,000 |
| Won deal reverted to open pipeline | 3 | −$253,000 |
| Won deal changed to Closed Lost | 1 | −$110,000 |
| Deal value lowered | 1 | −$35,000 |
| **Unexplained** | | **$0** |

Closed quarters are read from the extract taken at their close; the current quarter from the latest extract. Every answer says which.

**The three core questions**
- *Enterprise vs quota this quarter:* 4.5% booked, but 1.03x with open pipeline: covered, with no room for slippage. Its only booking moved in from Q1; 2 of 4 reps are short.
- *Reps at risk of missing Q2:* 8 of 10 (closed-won + open pipeline below quota). Largest gap: Priya Patel, $260,000.
- *Q2 vs the same point in Q1:* 8.4% of quota against 21.3% at day 32; 2.3% excluding deals moved from Q1.

**Definitions used:** "this quarter" = Q2 2026 (data as of May 2, 2026); "same point" = the same number of days into each quarter; "at risk" = closed-won + open pipeline below quota, with pipeline counted at full value.

---

## Glossary

| Term | Meaning |
|---|---|
| Quota | The sales target for a rep or segment for the quarter. |
| Bookings / closed-won | Deals already won, counted at their value. |
| Attainment | Bookings divided by quota. |
| Open pipeline | Deals still being worked that are expected to close this quarter. |
| Coverage | Bookings plus open pipeline, divided by quota. 1.00x means the target is met if every open deal closes. |
| Segment | A customer group: Enterprise, Mid-Market or SMB. |
| Extract / snapshot | A copy of the sales data taken on a given date. There are two: end of Q1, and May 2. |
| Trust Flag | A warning about something in the data that could mislead you about a specific answer. |
| Naive tool | For comparison only: what a quick AI tool would say taking the file at face value. |

---

## How it's built (technical)

Python standard-library web server plus pandas. No web framework, no API key.

**Design in brief**
- The query interpretation layer is separate from the business metric layer.
- Calculations are deterministic. No LLM-generated arithmetic.
- Built for the three required query patterns, plus pipeline coverage, deal lists and follow-ups; anything else is refused.
- An LLM is optional and only interprets the question. The answer text and explanations are generated by code from the computed figures.
- Every figure is traceable to its source rows, and flags are explicit.

1. **Parse** (`meridian/query.py`): question → structured query, fields as written.
2. **Validate** (`validate.py`): check against the roster and `snapshots.json`, or reject with every problem listed. No deal data read yet.
3. **Select** (`selection.py`): rows chosen by readable filters; rows failing load checks are set aside and named, never counted.
4. **Compute** (`metrics.py`): figures, formulas, assumptions, flags.
5. **Render** (`serialize.py`): JSON for the page, which formats and computes nothing itself.

| Path | What it is |
|---|---|
| `app.py` | Web server; opens the browser app |
| `web/index.html` | The browser app |
| `meridian/` | Engine: parsing, validation, selection, metrics, flags, reconciliation |
| `Q1/`, `Q2/`, `snapshots.json` | Data extracts and the date each represents |
| `tests/` | 142 tests |
| `ask.py` | Same answers in the terminal: `python ask.py "Why is Priya at risk?"` |
| `Dockerfile`, `fly.toml` | Fly.io deployment |

**Tests:** `pip install pytest`, then `python -m pytest -q`.

**Deploy (Fly.io):**

```bash
fly launch --no-deploy --copy-config
fly secrets set MERIDIAN_PASSWORD=<password>   # basic auth, any username
fly deploy
```
