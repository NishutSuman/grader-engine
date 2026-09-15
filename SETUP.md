# GradePilot — local setup

This is the FastAPI + SQLite grading platform (not the old `runs/`-based CLI
pipeline described in README.md/AGENT_GUIDE.md — those cover an earlier tool).

## 1. Clone the code

```bash
git clone https://github.com/NishutSuman/grader-engine.git
cd grader-engine
```

## 2. Python environment

Needs **Python 3.14**. Do not copy a `.venv/` between machines — always create
your own:

```bash
python3 -m venv .venv
source .venv/bin/activate          # .venv\Scripts\activate on Windows
pip install -r requirements.txt
```

## 3. Secrets

```bash
cp .env.example .env
```

Fill in `.env` with the values handed over separately (never via git — see
`.env.example` for what each key is for). In particular:

- `METABASE_SESSION` — get your **own** session value by logging into Metabase
  yourself; don't just reuse someone else's, it's a login cookie, not an API key.
- The Google Drive service-account JSON key — copy it into the repo (e.g.
  `runs/<any-folder>/sa_key.json`); the code finds it automatically by
  searching for that filename pattern.

## 4. Data + database

Copy these into the repo root (handed over separately, not via git — see the
handover package):

- `gradepilot.db` — the SQLite database (all evals, students, grades, tickets)
- `data/` — downloaded submissions + local report-card copies per eval

Both paths are also overridable via `GRADEPILOT_DB` / `GRADEPILOT_DATA` env
vars if you want them to live somewhere else.

## 5. Run it

```bash
.venv/bin/uvicorn app.web.main:app --reload --port 8000
```

Open http://localhost:8000

## 6. Smoke test

- Open an existing eval, load its student list.
- Open one report card — should redirect to the S3-hosted PDF.
- Try a "regen card" or small regrade on one student to confirm the Anthropic
  key and S3 upload both work end to end.
