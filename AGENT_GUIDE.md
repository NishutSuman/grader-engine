# Agent Guide — running a grading job

This is the checklist the agent (Claude) follows when the user hands over a new
assignment to grade. The goal each time: turn a **problem statement + submission
sheet (+ optional rubric)** into **per-student report cards**.

Nothing about the assignment is hardcoded — it all goes into one `config.yaml`.

---

## Inputs the user provides

1. **Problem statement** — any format (Markdown, PDF text, pasted). Any domain.
2. **Submission sheet** — a CSV where each student's per-part submissions appear
   as GitHub repo links and/or inline text.
3. **Rubric** — optional. If given, use it. If not, **draft one from the problem
   statement and get the user to confirm before grading.**

## Step 0 — Set up the run directory

Pick a short `<slug>` and create `runs/<slug>/`. Save the problem statement as
`runs/<slug>/problem_statement.md` and the submission sheet as
`runs/<slug>/submissions_raw.csv` (or set a different name in `sheet.raw_file`).

## Step 1 — Write `runs/<slug>/config.yaml`

Start from [`config/assignment.example.yaml`](config/assignment.example.yaml).
Fill in:

- **assignment**: slug, title, `total_marks` (must equal the sum of part marks),
  `normalize_to`.
- **branding**: `header_left` / `header_right` (PDF header), `author`,
  `drive_folder`, `sheet_title`. Ask the user for their branding once and reuse.
- **sheet**: inspect the raw CSV headers and map them.
  - `student_id_col`, `extra_cols`, optional `time_col`.
  - **Layout `long`** (one row per student *per question*): set
    `question_id_col`, `answer_col`, and `question_map` (question id -> part key).
  - **Layout `wide`** (one row per student, a column per part): set `column:` on
    each part instead of `question_map`.
- **submissions.types**: `[github, inline_text]`. Drop `github` to skip cloning.
- **parts**: one entry per gradeable part — `key`, `title` (use "Part N — Title"),
  `total_marks`, `breakdown` (components -> marks, **must sum to total_marks**),
  optional `required_files` (github only) and `problem_statement` (per-part task
  text; improves grading — paste the relevant section here).

Validate before proceeding:

```bash
python -m grader.config --run runs/<slug>
```

If a rubric wasn't provided, this is where you present the drafted `parts`/
`breakdown` to the user and adjust per their feedback.

## Step 2 — Clean the sheet

```bash
python -m grader.clean_submissions --run runs/<slug>
```

Review the printed summary (how many submitted, inline vs github, edge cases).
Produces `submissions_clean.csv`.

## Step 3 — Clone repos (skip if no github submissions)

```bash
python -m grader.clone_repos --run runs/<slug>
```

## Step 4 — Grade

Always dry-run first to sanity-check the prompts (no API cost):

```bash
python -m grader.grade_batch --run runs/<slug> --dry-run   # writes runs/<slug>/prompts/*.txt
python -m grader.grade_batch --run runs/<slug>             # submit the batch
python -m grader.collect_results --run runs/<slug>         # poll + write reports
```

`collect_results` waits for the batch to finish, then writes
`grading_reports/<student>.md` and `grading_summary.csv`.

## Step 5 — Master CSV + report cards

```bash
python -m grader.build_master_csv --run runs/<slug>   # grading_master.csv
python -m grader.report_pdf       --run runs/<slug>   # report_cards/*.pdf
```

## Step 6 — Distribute (optional, Google Drive + Sheet)

```bash
python notebook/make_notebook.py --run runs/<slug>    # runs/<slug>/Grade_Reports.ipynb
cd runs/<slug> && zip -r grading_reports.zip grading_reports/
```

Hand the user the notebook to run in Colab (it uploads the PDFs to the Drive
folder and builds the Sheet of public links).

---

## Notes

- **Re-grading a subset:** `grade_batch --student A,B,C` then re-run
  `collect_results` / `build_master_csv` / `report_pdf`.
- **Report-card edge cases** are handled automatically and shown as notices on the
  card: not submitted, web/Pages URL (not a repo), empty repo, inline text,
  private/deleted repo. See [`samples/report_cards/`](samples/report_cards/) for
  what each looks like.
- **Model:** set `model.grader_model` in config (default `claude-haiku-4-5` via
  the Batch API — cost-efficient for large cohorts).
- **Privacy:** everything under `runs/` is gitignored. Never commit student data.
