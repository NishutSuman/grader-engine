# Report Card Grader

A generic, config-driven agent for grading student assignment submissions and
producing per-student PDF report cards — for **any domain**.

Each grading job needs three things:

1. a **problem statement**,
2. a **submission sheet** (CSV) with each student's submissions, and
3. a **rubric** (marks breakdown) — or the agent drafts one from the problem
   statement for you to confirm.

The tool then grades every submission against the rubric with Claude, writes a
Markdown report and a PDF report card per student, a master CSV, and (optionally)
uploads the PDFs to Google Drive with a Google Sheet of links.

> Originally built for one DBMS assignment; now generalized. The original
> assignment is preserved as a worked example in [`examples/dbms-codejudge/`](examples/dbms-codejudge/).

---

## How it works

All assignment-specific knowledge (rubric, branding, how to read the sheet) lives
in a per-job **`config.yaml`**. The Python code is domain-agnostic and reads
everything from that config. Each job is a self-contained directory under
`runs/<slug>/`.

```
grader/            reusable, config-driven pipeline (a Python package)
  config.py        loads + validates config.yaml (single source of truth)
  clean_submissions.py   raw sheet -> one-row-per-student CSV
  clone_repos.py         clone GitHub submissions (skipped if none)
  grade_batch.py         build rubric prompts -> submit Anthropic batch
  collect_results.py     poll batch -> parse -> per-student .md reports
  build_master_csv.py    merge everything -> grading_master.csv
  report_pdf.py          render PDF report cards
notebook/
  make_notebook.py       build a Colab notebook: PDFs -> Drive + Sheet of links
config/
  assignment.example.yaml   annotated config template
examples/
  dbms-codejudge/    the original assignment as a reference (config + dataset)
samples/report_cards/  sample PDFs of each report-card type (local reference)
runs/                per-job working dirs (gitignored — holds student data)
```

Supported submission types: **GitHub repo links** and **inline text** in the sheet.

---

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate   # recommended
pip install -r requirements.txt
echo 'ANTHROPIC_API_KEY=sk-ant-...' > .env      # required
echo 'GITHUB_TOKEN=ghp_...' >> .env             # optional, raises GitHub rate limit
```

## Run a job

See [`AGENT_GUIDE.md`](AGENT_GUIDE.md) for the full step-by-step (this is what
Claude follows). In short, once `runs/<slug>/config.yaml` + `submissions_raw.csv`
are in place:

```bash
RUN=runs/my-assignment
python -m grader.config           --run $RUN     # validate config
python -m grader.clean_submissions --run $RUN
python -m grader.clone_repos      --run $RUN     # skipped if no github submissions
python -m grader.grade_batch      --run $RUN --dry-run   # inspect prompts first
python -m grader.grade_batch      --run $RUN     # submit for real
python -m grader.collect_results  --run $RUN
python -m grader.build_master_csv --run $RUN
python -m grader.report_pdf       --run $RUN     # -> runs/<slug>/report_cards/*.pdf
python notebook/make_notebook.py  --run $RUN     # -> runs/<slug>/Grade_Reports.ipynb
```

Then open the generated notebook in Google Colab to upload PDFs to Drive and
build the Sheet of links.

---

## Privacy

`runs/` and `samples/` are gitignored — student submissions, reports, and report
cards never get committed. Only the generic code and `examples/` (assignment
definitions, no student data) are meant to be pushed to a public repo. Review a
`git status` before your first push.
