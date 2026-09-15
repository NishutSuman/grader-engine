# GradePilot — The Grading Engine, Explained

_An approval document: what the engine does at every step, and **why** each decision was made.
Prepared 2026‑07. Companion cost analysis: `grading-cost-and-consistency.md`, `grading-cost-breakdown.md`._

---

## 0. What this engine is for

GradePilot grades student capstone/assignment submissions with Claude, producing scores that are:

- **Consistent** — the same submission grades to the same score, run after run (the hard problem with LLMs).
- **Defensible** — every score is backed by cited evidence from the submission, against the rubric students were given.
- **Fair** — students are judged only on what was disclosed to them; released scores are never silently lowered.
- **Auditable** — every score and every change is logged, and each student gets a durable report card.
- **Cost‑effective** — ~3–4× cheaper than manual grading, with the cost/method chosen per eval.

The rest of this document walks the full pipeline and explains the logic behind each stage.

---

## 1. The pipeline at a glance

```
1. Rubric setup      → what students are graded against (their disclosed rubric)
2. Dataset grounding → the real data behind the questions (ground truth)
3. Submission intake → students + their links, cleaned & mapped
4. Download + QC     → fetch every submission, verify what actually arrived
5. Sample grading    → measure real cost + consistency → recommend model/method
6. Grading (median-N)→ the consistent, evidence-cited scoring
7. QC + distribution → review, report cards, push scores, disputes
```

Each stage is a resumable, auditable step. State lives in a database (source of truth); files (downloads, cards) live on disk/S3 and are referenced by path.

---

## 2. Rubric — grading against what students were told

**Principle:** a rubric shared with students is a contract. We grade on *that*, not a stricter private version.

- **Use the paper's own rubric (default).** If the question paper already contains a mark scheme, the engine
  lifts it **verbatim** into the grading rubric (`rubric_ai.extract_rubric`). No criteria added, no bar raised.
- **Generate only if absent.** If the paper has no rubric, the engine authors a weightage‑calibrated one
  (`generate_rubric`) — lighter for low‑stakes evals, more discriminating for high‑stakes — but never invents
  requirements beyond what the paper asks.
- **Weightage posture.** Low weightage (≤10%) → lenient; moderate (>10–20%) → balanced; high (>20%, gates
  certification) → discriminating. Difficulty comes from the *questions*; the rubric only calibrates *how marks
  are awarded*.

### 2.1 Rubric atomisation (grader‑internal granularity)
A coarse criterion ("Data‑quality audit — 5") forces a fuzzy 0–5 judgement, which varies run to run. The engine
**decomposes each criterion into 2–4 near‑binary, checkable atoms** whose marks sum exactly to the criterion
(`rubric_ai.atomize_rubric`), e.g.:

> _Data‑quality audit — 5_ → duplicates found (1) · missing values with real counts (1) · outliers flagged (1) ·
> treatment recommended (1) · treatment applied in code (1)

- **Marks are preserved exactly** (atoms → criterion → section → total). Verified programmatically before use.
- **Nothing is added** — atomisation only makes the *disclosed* criteria checkable; it never introduces a new
  hurdle (same ethical rule as §2). This is what shrinks per‑run variance at the source.
- **Human‑gated.** Atomisation is generated as a draft, reviewed, and enabled explicitly before it grades anyone.

---

## 3. Dataset grounding — checking answers against reality

Many evals give students an external dataset (a Drive folder of CSV/Excel files). Without it, the grader can
only check a submission's internal consistency — it can't tell that a claimed "12% churn / 8,234 rows" is
fabricated.

**Logic (analyse once, reuse):**
1. **Detect** dataset links in the problem statement (Drive/CSV/xlsx), user‑confirmed.
2. **Download once** into `data/<slug>/dataset/`.
3. **Profile** the files with real statistics — row counts, columns/types, null counts, distributions, join keys,
   sample rows (`grader/dataset.py`).
4. **Inject** a compact, token‑budgeted brief into **every** grading call as ground truth (same brief for all
   students → cheap + fair). Raw files are never re‑sent per call.
5. **Reference findings (optional, reviewed).** The engine can draft the verifiable expected answers (actual
   duplicate count, real churn rate) — but these enter grading **only after a human approves them**, because an
   AI‑computed reference could itself be wrong.

Grounding makes both the score *and* the feedback check against the actual data.

---

## 4. Submission intake — mapping messy sheets

Submission sheets (from OPs/Metabase) are inconsistent, so intake is **value‑aware**, not header‑trusting
(`grader/intake.py`):

- **Column detection** scores each column on its header **and its cell values** (email regex, ID pattern,
  name pattern, link presence). This fixes real failures like an ID‑holding "Username" column being mistaken for
  the name field.
- **Link cleaning** strips HTML (`<a href>`, entity‑encoded) from Metabase exports and pulls every URL.
- **Multi‑link stacking** — if a student submitted several links (one per part), they're kept together and each
  is downloaded.
- **Full roster** — every student on the sheet is brought in, including no‑submission ones (shown as such), so
  nobody is silently dropped.

---

## 5. Download + QC — fetch, then verify

- **Download** uses a Google **service account** (bypasses per‑IP throttling), plus GitHub clone, S3/URL, Google
  Doc export. Parallel, resumable, per‑link status recorded.
- **Zip extraction** — students sometimes commit their whole submission as a `.zip` in the repo. The reader
  unpacks archives (guarded against zip‑bombs/path‑traversal) so the source inside gets graded.
- **Oversized handling** — image‑heavy PDFs are downscaled (~120 DPI) before grading; the grading call retries
  smaller on any size error, so a huge submission never blocks the run.
- **Download QC (read‑only).** Before grading, a one‑click check cross‑references each link's status **against the
  files actually on disk** — flagging empty repos, failed/restricted links, and "ok but zero files" mismatches.
  It never re‑downloads. Acceptable partials (e.g. one intentionally‑empty repo) can be **passed** (marked
  verified) so they enter grading; the approval is logged.

---

## 6. The grading method — where consistency comes from

### 6.1 Why LLM grading varies, and what we can control
LLM output is non‑deterministic. On modern Claude models **`temperature` is deprecated** — we cannot force
deterministic sampling like a local model can. So consistency is engineered two ways: **reduce the noise at the
source** (atomised rubric + dataset grounding), and **release a statistic that is stable despite the noise** (the
median of several runs).

### 6.2 Model choice — decided by measurement, not price
We built a **consistency harness** that grades the same submissions many times and measures the score variance.
Results (5 submissions × 9 runs, /100):

| Model | Single‑run SD | **Median‑of‑3 SD** | Degenerate (all‑zero) runs | Verdict |
|---|---|---|---|---|
| **Sonnet 4.6** | 0.62 | **0.21** | none | ✅ chosen default |
| Sonnet 5 | 4.9 | 0.69 | ~1 in 6 | ✅ cost‑saver fallback |
| Haiku 4.5 | 12.1 | 6.32 | frequent, bimodal | ❌ rejected |

**Sonnet 4.6 is near‑deterministic** (one submission scored identically nine times). The "cheap" model (Haiku)
was rejected on evidence — it graded the same work ~16 on most runs and ~95 on others. **Cost never overrides
consistency.**

### 6.3 Median‑of‑N ensemble (the core mechanism)
Each student is graded **N times** (N=3 default, **N=5 for high‑stakes**) and the **median** per section is
released (`grader/grade.py::_ensemble`):

- **1 full grade** (scores + cited evidence + feedback) **+ (N−1) score‑only runs** (numbers only) on
  **prompt‑cached input** — so re‑runs cost ~10% of the input, not 3×.
- **Median per section**, summed to the total. The median **structurally rejects outliers**: a lone bad/degenerate
  run (all‑zero, or wildly off) is ignored, not averaged in.
- **Degenerate‑run rejection** — all‑zero runs are dropped when other runs disagree. _(Real example: a student's
  first grade returned 0 with positive feedback; the ensemble/regrade returned 56 — the true score.)_
- **Regrades use the same ensemble**, so a dispute re‑grade lands within ~0.2 of the original median — the score
  barely moves, by construction.

### 6.4 Evidence‑cited, anti‑hallucination scoring
Every grading call is built from a fixed template with in‑prompt QC rules: grade **only** what is present; **every
deduction must cite a quote + file/section** (or state "not present"); no full marks for generic/unshown work;
score each atom independently. Per‑section evidence is stored with the grade — the basis for defensible dispute
responses.

### 6.5 Sample grading → recommendation (adaptive, before the paid run)
Before grading a whole cohort, the engine **grades 3 random submissions ×3** to measure **this eval's** real token
usage, time, and consistency, then recommends the model + method (`grader/preview.py` + `grader/costing.py`),
saving the full comparison to the DB. The recommendation balances, in order:

1. **Consistency (hard gate)** — only models that pass the harness are eligible.
2. **Stakes** — high‑weightage evals always take the most consistent, degenerate‑free option.
3. **Cost** — for low‑stakes evals, a cheaper *consistent* model is recommended when it saves materially.

So Sonnet 4.6 is the default, with Sonnet 5 surfaced as a cost‑saver where the stakes allow — the engine keeps
cost, consistency, submission type, and stakes all in view.

---

## 7. QC, distribution & disputes

- **Report cards** — each graded student gets a PDF card (name, email, per‑section scores, feedback) uploaded to
  **S3 with a durable, student‑viewable link**. Re‑grading overwrites the same link.
- **Push to sheet** — scores are written to a `<slug>-grades` tab for OPs.
- **Dispute handling** — a released score is answered first from its **stored evidence**; only the challenged
  section is re‑graded, using the same ensemble.
- **No‑decrease + delta bands (governance).** Because residual variance can never be zero on a hosted model: a
  regrade within a small band keeps the released score; a released score is **never lowered**; only a regrade that
  exceeds the band (a genuine first‑pass fault) raises it, with a fresh card issued. This makes disputes safe.

---

## 8. Robustness — nothing silently fails

| Failure mode | Handling |
|---|---|
| Response too large (413) | Progressively shrink payload (drop page‑images, trim text) and retry |
| Image‑heavy / huge PDF | Auto‑downscale to ~120 DPI before grading |
| Zipped submission | Unpack archives, grade the source inside |
| Degenerate all‑zero grade | Rejected by the median / regrade |
| Truncated output | Retry at higher token budget |
| Slow run / live feedback | Per‑student commit → live progress bar, count, per‑student log, and ETA |
| Stale/duplicate intake | One‑click "clear & re‑fetch"; value‑aware mapping prevents the duplication |

---

## 9. Cost — for the budget conversation

Recommended setting (**Sonnet 4.6 × median‑of‑3**, optimised with caching + score‑only runs), ₹100/USD:

| | Per submission | vs Manual (₹100) |
|---|---|---|
| Code repo | ₹25.9 | **3.9× cheaper** |
| PDF (image‑heavy) | ₹33.7 | 3.0× cheaper |

The ensemble adds only **~27%** over single‑grading (not 3×), thanks to prompt caching + score‑only re‑runs.
Full per‑model tables and weekly/monthly projections are in the companion cost docs. _Actual spend to date: 896
submissions across 5 evals cost ~₹16k on AI vs ~₹90k manual (~82% saved)._

---

## 10. Auditability & governance

- **Every score mutation is logged** (import, grade, QC pass, regrade, sheet push) in an immutable audit trail.
- **Ensemble provenance** — each grade stores its N runs, the dropped‑degenerate count, and the run‑to‑run SD.
- **Report cards on S3** give students a durable, verifiable record.
- **Human review gates** — atomised rubric, dataset reference findings, and download‑QC passes all require explicit
  approval before they affect a grade.

---

## 11. Why this is approvable

| Requirement | How the engine meets it |
|---|---|
| **Consistent** | Median‑of‑3 on Sonnet 4.6 → released‑score SD ~0.2/100; regrades reproduce it |
| **Fair** | Graded only on the student‑disclosed rubric; atomisation/grounding add rigor, not hurdles |
| **Defensible** | Every deduction cites evidence; per‑section feedback stored; dispute answers grounded |
| **Safe on disputes** | No released score is ever lowered; regrades within a delta band hold |
| **Robust** | 413, oversized, zipped, degenerate, and truncated cases all handled, never silently dropped |
| **Auditable** | Full audit log + ensemble provenance + durable S3 report cards |
| **Cost‑effective** | ~3–4× cheaper than manual, method chosen per eval by measured cost + consistency |

**In one line:** the engine grades every student the same way twice, only on what they were told, with the maths
shown — and proves it with measured variance, a full audit trail, and a report card per student.
