# BITSoM PMAI-3 Capstone — AI Grading Side

Handover context for the BITSoM x Masai "Product Management with Generative & Agentic AI"
Cohort 3 capstone AI-grading project. This is deliberately kept **outside GradePilot's own
app/DB** — read "Why this is separate from the app" below before assuming anything here
should be wired into `app/` or `grader/`.

## What this project actually is

Capstone is 40 marks across 3 phases: Phase 1 (10, ideation — graded elsewhere, not ours),
Phase 2 (20, "Project Submission"), Phase 3 (10, "Project Presentation," a live call).

**Grading is two-track and secret on one side.** 4-6 external mentors grade 100% manually
(paid per submission, same mentors run the Phase 3 live calls). AI-assisted grading is
explicitly prohibited for them, and they are **not told** an AI grading/report-card pass is
also happening in parallel. Nothing mentor-facing may ever reference this project.

**Why this is separate from the app:** the user explicitly asked for this to be kept out of
GradePilot's Eval/Student/Grade DB and out of the web UI. Nothing here creates rows in
`gradepilot.db`. All state lives in the 4 Google Sheets themselves (mentor-entered scores/
feedback, plus 2-3 computed columns this project adds) and in S3 (rendered report card PDFs,
under the `bitsom-pmai3-capstone/` prefix, separate from GradePilot's own `masai-capstone-
gradepilot/` prefix). Auth reuses the same service-account credentials already set up for the
main app (see the repo's own `SETUP.md`) — nothing extra to configure.

## The 4 sheets

One standalone Google Sheet per problem statement, each shared directly with the service
account (not part of the master "Outcomes" sheet used elsewhere in this repo):

| PS | Title | Sheet ID | Problem statement |
|---|---|---|---|
| PS1 | "PS 1 - TA 1" | `12Pea4bedP1pA2bZyEBDJ3CTP5vIIUoosUaLtsbfSa7s` | Better access to reliable health information for non-English-speaking or underserved communities using voice- and text-based AI agents |
| PS2 | "PS 2 - TA 2/ TA 5" | `1c5aolEYIL38EkYTYSWnDuzR7cQVOMfIuyQGEiWdfSgw` | Simplifying travel planning via a multi-agent system (research, bookings, budget optimisation, contextual recommendations) |
| PS3 | "PS 3 - TA 3 / TA5" | `1NzCbTfyZl6HsEfPgF5oZU8Z47Bz74dCT8RTCu5X2opo` | Improving financial inclusion for underserved/underbanked individuals via personalised, trustworthy financial guidance |
| PS4 | "PS 4 - TA 4 / TA6" | `1W_7J6NDPA72D04FlKOpJQYQerLCWLMg0hZwqaTVzvuU` | An AI agent that curates and delivers contextual company insights to sales reps before meetings |

These are the *only* one-line problem statements that exist anywhere — there is no fuller
brief. Confirmed directly with the user; don't go looking for one.

### Sheet structure

Each spreadsheet has 6 tabs. **Only `Group-Mapping` matters.** `Group-Mapping_29th&30th` and
`Sheet8` are explicitly not used (user confirmed, leave them alone). `Form responses 1`/`2`
are the raw student submission/scheduling forms, background only.

`Group-Mapping` is one row per student (not per group — a group's members each get their own
row). Header is 3 rows deep (row 1 = category banner, row 2 = column name, row 3 = atomized
criterion text for the score columns). Data starts at row 4. Column layout is *not* identical
across all 4 sheets — PS3 is offset by 1 column versus the others — so every script here
resolves column positions by searching header text at runtime (see `sheet_io.find_columns`),
never by hardcoded column letters.

**The live rubric in this sheet is the ground truth**, not the originally-designed rubric
artifact (https://claude.ai/code/artifact/90eb2541-53f3-4af0-b52c-9231639854c4). They diverge:
the artifact's Phase 3 weights were Live MVP Demo (3) / Clarity (2) / Defense Under Q&A (3) /
Participation (1) / Structure (1); the **live sheet** actually uses Live MVP Demo (1) /
Clarity (2) / Defense Under Q&A (5) / Participation (1) / Structure (1) — same 10-mark total,
very different distribution. Trust the sheet.

Key columns in `Group-Mapping` (indices resolved at runtime, not fixed):
- `Group Tag` (col G / index 6): `Individual:<name>` for solo submissions, `Group:<teamname>`
  for team submissions. **Don't trust this alone for feedback-formatting decisions** — see
  "Known issue #1" below.
- `Grader` / `Grader IM` (col M / index 12): which mentor graded this row. Sparse — PS2 has
  **zero** values here at all, no per-mentor attribution possible for that sheet.
- Phase 2 sub-criterion columns (8 criteria, 26 total graded sub-items, max 20)
- `Detailed Submission Feedback`: free-text Phase 2 feedback
- Phase 3 sub-criterion columns (5 criteria / 8 graded sub-items, max 10)
- `Detailed Presentation Feedback`: free-text Phase 3 feedback. For real groups, mentors
  often (not always — see known issues) write this with explicit `Group Feedback` / `Individual
  Feedback` labels inside the same cell.

## Where this actually stands (2026-09-17)

### Done
1. **Two computed columns added to all 4 sheets**: `Phase 2 Score (computed)` and
   `Phase 3 Score (computed)`, appended right after each sheet's last existing column. No
   total column existed before this — mentors only fill in individual sub-criterion cells.
   Script: `compute_and_write_sums.py`. Already run once against live data; re-run any time
   mentors add more grades (it's idempotent, recomputes fresh each run).
2. **Batch statistics** — see "Score statistics" below. Script: `analyze_scores.py`.
3. **Report card generation approach validated on 10 students**, across two rounds:
   - **Round 1** (8 solo students, picked by chance — see samples table below): found 3 real
     defects on manual re-verification against raw mentor text. (a) Aditi Moudgalya's card
     said "impressive overall effort **from the team**" — fabricated, her raw text never
     mentions a team, and she submitted solo. (b) Shivanand Shukla's card literally printed
     the placeholder string `<UNKNOWN>` into a real PDF. (c) Yatheshta Vijay's card silently
     dropped 2 of 4 real mentor improvement points. Root cause: the prompt unconditionally
     forced every Phase 3 section into a "Group Performance / Individual Performance" split,
     which breaks when a submission has no real group/individual distinction to make (i.e.
     nearly all of round 1's picks, since they happened to all be solo).
   - **Round 2** (same 8 + 2 genuine group members added): fixed by branching the prompt on
     whether the raw text has explicit `Group Feedback`/`Individual Feedback` labels, instead
     of forcing the split unconditionally. Re-verified all 10 against raw source manually —
     clean, no defects found. Current generator script (`generate_report_cards.py`)
     implements this fixed version.
   - Sample cards from round 2 (the corrected ones — trust these over round 1's, which are
     still up in S3 for reference but contain the 3 known bugs above):

     | PS | Student | Type | Phase 2 | Phase 3 | Total | Card |
     |---|---|---|---|---|---|---|
     | PS1 | Rajeswari Majumder | Solo | 13.75/20 | 5/10 | 18.75/30 | [link](https://s13n-curr-images-bucket.s3.ap-south-1.amazonaws.com/bitsom-pmai3-capstone/samples_v2/PS1_bitsom_pm_2601304.pdf) |
     | PS1 | Mohanraam Ravichandran Pillai | Solo | 7/20 | 6.5/10 | 13.5/30 | [link](https://s13n-curr-images-bucket.s3.ap-south-1.amazonaws.com/bitsom-pmai3-capstone/samples_v2/PS1_bitsom_pm_2601777.pdf) |
     | PS2 | Aditi Moudgalya | Solo | 15.2/20 | 8.7/10 | 23.9/30 | [link](https://s13n-curr-images-bucket.s3.ap-south-1.amazonaws.com/bitsom-pmai3-capstone/samples_v2/PS2_bitsom_pm_2601336.pdf) |
     | PS2 | Akshay K H | Solo | 12.1/20 | 8.3/10 | 20.4/30 | [link](https://s13n-curr-images-bucket.s3.ap-south-1.amazonaws.com/bitsom-pmai3-capstone/samples_v2/PS2_bitsom_pm_2601643.pdf) |
     | PS3 | Kodityala Sutha Sri | Solo | 8/20 | 7.2/10 | 15.2/30 | [link](https://s13n-curr-images-bucket.s3.ap-south-1.amazonaws.com/bitsom-pmai3-capstone/samples_v2/PS3_bitsom_pm_2601986.pdf) |
     | PS3 | Shivanand Shukla | Solo | 12/20 | 7.3/10 | 19.3/30 | [link](https://s13n-curr-images-bucket.s3.ap-south-1.amazonaws.com/bitsom-pmai3-capstone/samples_v2/PS3_bitsom_pm_26011105.pdf) |
     | PS4 | Anoupama BR | Solo | 12/20 | 6/10 | 18/30 | [link](https://s13n-curr-images-bucket.s3.ap-south-1.amazonaws.com/bitsom-pmai3-capstone/samples_v2/PS4_bitsom_pm_2601115.pdf) |
     | PS4 | Yatheshta Vijay | Solo | 12.5/20 | 6/10 | 18.5/30 | [link](https://s13n-curr-images-bucket.s3.ap-south-1.amazonaws.com/bitsom-pmai3-capstone/samples_v2/PS4_bitsom_pm_2601878.pdf) |
     | PS4 | Bhushan Koussadikar | Group:MEDEDGE | 13.5/20 | 7.8/10 | 21.3/30 | [link](https://s13n-curr-images-bucket.s3.ap-south-1.amazonaws.com/bitsom-pmai3-capstone/samples_v2/PS4_bitsom_pm_2601197.pdf) |
     | PS4 | Vedakanth Vajjela | Group:PITCHPILOT_PS 4 | 8.5/20 | 6.2/10 | 14.7/30 | [link](https://s13n-curr-images-bucket.s3.ap-south-1.amazonaws.com/bitsom-pmai3-capstone/samples_v2/PS4_bitsom_pm_2601292.pdf) |

### Known issues — NOT fixed, read before running the full batch

Found by scanning all 420 phase-3-scored rows across all 4 sheets (not just the 10 samples):

1. **189 of 420 "Group:"-tagged rows (45%) have no `Group Feedback`/`Individual Feedback`
   labels in their raw text at all.** `generate_report_cards.py`'s current logic branches on
   whether those labels are *actually present in the text*, not on the `Group Tag` metadata
   — which should handle this correctly (falls back to a single unified feedback block,
   same as a solo student) — but **this fix has only been reasoned through, not re-run and
   manually verified against real output** the way round 1→2 was. Do that verification
   before trusting it at scale.
2. **49 rows have a real Phase 3 score but completely empty feedback text.** Current script
   skips the LLM for these and writes "No written feedback was recorded for this section."
   instead of risking fabrication — also not yet manually spot-checked against real output.
3. **Genuinely unresolved, not something the script tries to solve**: some of the 189
   label-less group rows contain ONE feedback blob describing MULTIPLE named individuals at
   once (e.g. one COSMIC OBSERVERS row reads "She handled both mentor questions
   independently... He delivered the opening segment... She presented the GTM..." — three
   different people in one cell). Every member of that group would receive the identical
   blob under the current "no labels → unified feedback" fallback. That's honest (nothing
   invented) but not personalized to each individual. This needs a human decision on how to
   handle it — possibly excluding these specific rows from Phase 3 feedback entirely, or
   manually splitting by name-matching — before the affected students' cards are finalized.
4. **The full ~630-row batch has not been run.** Only the 10 samples above exist.
5. **The Report Card Link column has not been written to any sheet yet**, not even for the
   10 samples — `generate_report_cards.py --write-links` does this (as a 3rd computed
   column, right after Phase 2/3 Score), but it's never actually been invoked.

### Recommended next steps, in order

1. Fix known issue #3 (the multi-person blob rows) — needs a decision from the user on how
   to handle those specific rows, not a code fix alone.
2. Re-verify known issues #1 and #2 with a fresh manual sample (deliberately including some
   label-less group rows and some empty-feedback rows this time), same method as the
   round 1→2 verification above: extract PDF text, compare claim-by-claim against the raw
   sheet cell, confirm nothing invented and nothing dropped.
3. Once clean, run `generate_report_cards.py --write-links` per sheet (or `--ps all`) for
   the full batch.
4. Consider following up on the score-distribution findings below (PS3's low average, and
   Mayank J's Phase 3 anomaly) with whoever manages the mentor relationships — this project
   surfaced them but they're a grading-quality question, not something this script can act on.

## Score statistics (as of 2026-09-17, via `analyze_scores.py`)

**Overall** (all 4 sheets, n=323 students with both phases scored): mean **19.02/30 (63.4%)**,
median 19.75, stdev 4.32. Phase 2 alone: mean 12.13/20 (60.7%, n=435). Phase 3 alone: mean
6.87/10 (68.7%, n=394).

**Per sheet:**

| Sheet | Phase 2 mean | Phase 3 mean | Combined mean | % |
|---|---|---|---|---|
| PS1 | 13.69/20 | 5.23/10 | 18.78/30 | 62.6% |
| PS2 | 12.88/20 | 7.41/10 | 20.15/30 | 67.2% |
| PS3 | 8.68/20 | 6.28/10 | 14.95/30 | 49.8% |
| PS4 | 13.01/20 | 7.19/10 | 20.53/30 | 68.4% |

**Per mentor:**

| Mentor | Sheet(s) | Phase 2 mean | Phase 3 mean | Combined mean |
|---|---|---|---|---|
| Param | PS4 | 12.94/20 | 7.25/10 | 20.52/30 |
| Anmoll | PS3 | 8.68/20 | 6.28/10 | 14.95/30 |
| Mayank J | PS1 | 13.69/20 | 4.75/10 | 18.78/30 |
| Deepanshu | PS4 | 13.21/20 | 6.82/10 | 20.59/30 |

Two findings worth someone's attention:
- **PS3's low average maps entirely to one mentor (Anmoll)** — PS3 has only one attributed
  grader in the data, so it's impossible to separate "PS3 is a genuinely harder problem
  statement" from "Anmoll grades harsher than the others" from this data alone.
- **Mayank J's grading is internally inconsistent**: his Phase 2 average (68.5%) is right in
  line with everyone else, but his Phase 3 average (47.5%) is far below every other mentor's
  Phase 3 number (all in the 63-74% range). Sharpest single anomaly in the dataset.
- Param and Deepanshu, both grading PS4, land almost identically (20.52 vs 20.59 combined) —
  a genuine same-problem-statement cross-mentor consistency check, and it's reassuring.
- PS2 has zero values in its "Grader" column, so no per-mentor breakdown is possible there.

## Scripts

All read live from the sheets each run (no cached/committed data files — avoids putting
student PII into git). Run each from the **Capstone Grader repo root**, not from inside this
folder:

- `sheet_config.py` / `sheet_io.py` — shared constants and read/write/column-resolution
  helpers. Every other script imports these.
- `compute_and_write_sums.py` — adds/refreshes the 2 computed score columns on all 4 sheets.
  Idempotent.
- `analyze_scores.py` — prints the overall/per-sheet/per-mentor statistics above. Read-only.
- `generate_report_cards.py` — the report card generator. `--ps PS1` (or `all`), `--limit N`
  to cap for testing, `--write-links` to also write the Report Card Link column back. See the
  big warning comment at the top of this file before running it on the full batch.
