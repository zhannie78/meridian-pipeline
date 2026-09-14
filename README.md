# Meridian Pipeline Intelligence

Ask plain-English pipeline questions. Every answer shows its source rows, formulas, assumptions and trust flags, so it can be recomputed by hand.

## Open the app in your browser

The app runs on your own computer and opens in your web browser. The terminal is only used to start it; everything else happens in the browser. Setup takes about 3 minutes.

### 1. Check you have Python 3.9 or newer

Open a terminal (**Mac:** Terminal app. **Windows:** PowerShell) and run:

| Mac | Windows |
|---|---|
| `python3 --version` | `py --version` |

If it prints `Python 3.9` or higher, continue. If not, install Python from https://www.python.org/downloads/ (on Windows, tick **"Add python.exe to PATH"** during install), then open a new terminal.

### 2. Go to the unzipped folder

Unzip the download. The folder is usually named `meridian-pipeline-main`. In the terminal, type `cd ` (with a space), drag the folder into the terminal window, and press Enter. For example:

```bash
cd ~/Downloads/meridian-pipeline-main
```

### 3. Install, then start the app

Copy and paste these lines one at a time.

**Mac:**

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

**Windows (PowerShell):**

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py
```

The first line creates a private Python environment inside the folder, so nothing is installed system-wide. The install step downloads pandas and takes about a minute.

### 4. Use the app

Your browser opens automatically at **http://127.0.0.1:8765**. If it doesn't, copy that address into your browser.

- The page opens on **The $1.99 M Bridge**, a reconciliation of the two data extracts.
- Click any suggested question on the left, or type your own.
- In each answer, click `[A]`-style links and deal IDs to see the exact source rows.

Leave the terminal window open while you use the app. To stop it, click the terminal and press **Ctrl+C**.

**To open it again later:** go to the folder (step 2), then run `source .venv/bin/activate` (Mac) or `.venv\Scripts\Activate.ps1` (Windows), then `python app.py`.

### If something goes wrong

| Problem | Fix |
|---|---|
| `command not found: python3` or `py` | Install Python (step 1) and open a new terminal. |
| Windows: "running scripts is disabled" | Run `Set-ExecutionPolicy -Scope Process Bypass`, then activate again. |
| `Address already in use` | Another program is using the port. Run `python app.py --port 9000` and open http://127.0.0.1:9000. |
| The browser didn't open | Copy http://127.0.0.1:8765 into your browser. |
| `pip install` fails | Run `python -m pip install --upgrade pip`, then try again. |

The app needs no API key and no internet connection once installed. It is only reachable from your own computer. This is the local application version. 

## Non-Technical Version
The above instructions lets you run the local version of the application. 

I've made it easy for non-technical business users to access via the following link by deploying a hosted web app: 

App Link: https://meridian-pipeline.fly.dev/ \
Password: **meridian-rpGX27WYLetp** 

## How it works

1. **Parse** (`query.py`): question → structured query, fields as written.
2. **Validate** (`validate.py`): check against the roster and `snapshots.json`, or reject with every problem listed. No deal data read yet.
3. **Select** (`selection.py`): rows chosen by readable filters; bad rows set aside, never counted.
4. **Compute** (`metrics.py`): figures, formulas, assumptions, flags.
5. **Render** (`serialize.py`): JSON for the page, which formats and computes nothing itself.

Starts with query interpretation layer separate from business metric layer. \
Deterministic calculations. No LLM generated arithmetic. Hard-coded support for the three required query patterns. \
LLM used only for interpreting the question and rendering the answer and explanation. \
Source traceability. Explicit flags.

## Key finding: Q1 isn't final

The two extracts disagree on Q1 bookings: $6,041,000 at Q1 close vs $4,050,000 on 2026-05-02. The app's opening page reconciles the $1,991,000 to the dollar (reused IDs, moved close dates, reversals). Closed quarters are read from the close extract; Q2 from the latest.

## Core queries

- **Enterprise vs quota:** 4.5% booked, but 1.03x with open pipeline: covered with no room for slippage. Its only booking moved in from Q1; 2 of 4 reps are short.
- **Reps at risk:** 8 of 10 (won + open pipeline < quota). Largest gap: Priya Patel, $260,000.
- **Q2 vs same point in Q1:** 8.4% vs 21.3% of quota at day 32; 2.3% excluding deals moved from Q1.

## Answer structure

1. Direct answer
2. Supporting Metrics
3. Source Data
4. Method (including Calculations)
5. Assumptions
6. Trust Flags

## Tests

With the environment active: `pip install pytest`, then `python -m pytest -q` (142 tests).

## Deploy (Fly.io)

```bash
fly launch --no-deploy --copy-config
fly secrets set MERIDIAN_PASSWORD=<password>   # basic auth, any username
fly deploy
```

## Limits

- The rule parser covers tested phrasings; anything else is refused.
- 10 reps, 92 deals, two CSV extracts. Not tested at real scale.
