# GradePilot — Grading Engine: Consistency & Cost Plan

_Prepared 2026‑07 · based on **measured** data from IITP‑AIML‑2506 (churn‑ML capstone, 182 GitHub‑repo submissions, dataset‑grounded). Consistency measured with the `grader/consistency.py` harness (5 identical submissions × 9 runs per model)._

This document defines **how we make LLM grading consistent** and **what it costs** across models,
optimisations, and submission types. Costs assume **₹100 / USD** (deliberately round & slightly high).

---

## 1. The problem, and the mechanism

LLM grading is non‑deterministic: the *same* submission + *same* rubric can score 65 one run and 72
the next. On modern Claude, **`temperature` is deprecated — we cannot force deterministic sampling**
(unlike a local Qwen VM). So the released score must be a statistic that is stable despite the noise:
the **median of N runs (min 3)**, plus a well‑engineered rubric so the noise is small to begin with.

### Model consistency — measured (this is the headline result)

Same 5 submissions, graded 9× each on the current (paper's‑own) rubric:

| Model | Single‑run SD | **Median‑of‑3 SD** | Worst spread | Degenerate runs? | Verdict |
|---|---|---|---|---|---|
| **Sonnet 4.6** | **0.62** | **0.21** | 4.5 | **none** | ✅ **Best — near‑deterministic** |
| Sonnet 5 | 4.9 | 0.69 | 68.5 | Yes (~1 in 6 on one sub) | ✅ OK with median‑of‑3 |
| Haiku 4.5 | 12.1 | 6.32 | 95.5 | Yes + **bimodal** (16 vs 95!) | ❌ **Unusable** |
| Opus 4.8 | _not tested_ | — | — | — | reserve for disputes |

Key takeaways:
- **Sonnet 4.6 is the clear winner** — single‑run SD 0.62 (already near‑deterministic), median‑of‑3
  SD 0.21, and **not a single degenerate blow‑up** in 45 runs. One submission scored 97.5 **nine
  times identically**.
- **Sonnet 5** is usable *only* with median‑of‑3: a single run is unsafe (~1 in 6 returned a clean‑
  but‑**all‑zero** grade; worst spread 68.5). Median‑of‑3 rejects a lone zero, but if 2 of 3 runs
  degenerate the median is still wrong (~7% risk on a degenerate‑prone submission).
- **Haiku 4.5 fails** — it is not just noisy, it's *bimodal* (grades the same work ~16 on most runs,
  ~95 on others). Median can't fix a 16‑vs‑95 split. The cheap model is a false economy here.

> **Model decision: grade on Sonnet 4.6.** It gives Qwen‑VM‑like stability (~±0.2 after median‑of‑3,
> no degenerate runs) on a hosted API. Sonnet 5 is the cost‑saver fallback; Haiku is ruled out.

---

## 2. Design parameters (the agreed spec)

### 2.1 Grading prompt — well‑defined, with QC points baked in
Every call is built from a fixed, ordered template so runs vary minimally:
1. **Role & standard** — impartial grader; rubric is ground truth; grade strictly & fairly.
2. **Anti‑hallucination / QC rules** (in‑prompt): grade only what's present; **every deduction cites
   a quote + file/section** or states "not present"; no full marks for generic/unshown work; score
   each atom independently.
3. **Dataset ground truth** (when present) — real schema, counts, distributions, reviewed reference findings.
4. **Atomised rubric** (§2.2).
5. **Submission content** — format‑optimised (§2.3).
6. Output via a forced structured tool: per‑atom `score` + `evidence[]` + `feedback` + overall.

### 2.2 Rubric granularisation (biggest source of remaining noise)
Even a coarse student‑facing rubric (_"Data‑quality audit — 5"_) is broken **internally** into atomic,
near‑binary checks summing to the same marks (counts identified 1 · duplicates 1 · outliers 1 ·
treatment recommended 1 · treatment applied 1). The total is summed **in code** → no aggregation
noise. **Ethical guardrail:** atomising only *decomposes* the disclosed criteria — it must never add
new requirements or raise the bar. Priority targets are the noisy sections (Q4 model /35, Q2 audit /20).

### 2.3 Per‑format handling
| Format | Handling | Effect |
|---|---|---|
| **Code repo** | Skip `node_modules/.git/venv/dist`, datasets, binaries; notebooks stripped of outputs; char budget. | Only gradeable source sent — lower tokens & noise. |
| **PDF (typed/text)** | Extract text; rasterise only low‑text/visual pages to 100‑DPI JPEG (cap 28). | Cheap; avoids the 413 limit. |
| **PDF (wireframe/scan‑heavy)** | Same, more pages → images (~1.3k tokens each). | Predictable; the PDF cost driver. |
| **Oversized PDFs** | Auto‑downscale (~120 DPI); `_grade_one` retries smaller on 413. | Never blocks grading. |

### 2.4 "Cheaper model" — validated, not assumed
Every candidate is run through the consistency harness before adoption. Result above: **the cheap
model (Haiku) failed**; the right lever was a *different Sonnet* (4.6), not a smaller model.

---

## 3. Optimisations — why "3× grading" is NOT 3× cost

| Lever | What it does | Saving |
|---|---|---|
| **Prompt caching** | Identical submission+rubric+dataset input across a student's N runs → runs 2..N pay **10%** (write 1.25×, read 0.10×). | ~45% off ensemble input |
| **Score‑only ensemble** | Extra runs output **just numbers** (~250 tok) for the median; full evidence generated **once**. | ~65% off ensemble output |
| **Adaptive N** | Grade 3; drop degenerate/outlier runs; escalate to 5 **only** on genuine disagreement. | Most students stay at 3 |
| **File/page skipping** | §2.3 — send only gradeable content. | Fewer input tokens |
| **Batch API (optional)** | Async, **‑50% on all tokens**, up to 24 h latency. | ‑50% if latency OK |

**Model pricing (per 1M tokens)** · **Per‑call tokens** (code = measured; PDF = estimated)

| Model | Input | Output | Cache write | Cache read | | Submission type | In | Out(full) | Out(score‑only) |
|---|---|---|---|---|---|---|---|---|---|
| Haiku 4.5 | $1 | $5 | $1.25 | $0.10 | | Code repo | 35k | 6.6k | 0.25k |
| Sonnet 5 (intro→08/26) | $2 | $10 | $2.50 | $0.20 | | PDF text | 30k | 5.0k | 0.25k |
| **Sonnet 4.6** | $3 | $15 | $3.75 | $0.30 | | PDF image‑heavy | 55k | 6.0k | 0.25k |
| Opus 4.8 | $5 | $25 | $6.25 | $0.50 | | | | | |

---

## 4. Cost per 1,000 students (optimised median‑of‑3, ₹100/USD)

### 4.1 Code repos _(measured basis)_
| Model | Single (1×) | Naive 3× | **Optimised median‑3** |
|---|---|---|---|
| Haiku 4.5 ❌ | ₹6.8k | ₹20.4k | ₹8.6k |
| Sonnet 5 | ₹13.6k | ₹40.8k | **₹17.3k** |
| **Sonnet 4.6** ✅ | ₹20.4k | ₹61.2k | **₹25.9k** |
| Opus 4.8 | ₹34.0k | ₹102k | ₹43.1k |

### 4.2 PDFs _(estimated)_ — optimised median‑3
| Model | PDF text | PDF image‑heavy |
|---|---|---|
| Haiku 4.5 ❌ | ₹7.1k | ₹11.2k |
| Sonnet 5 | ₹14.2k | ₹22.5k |
| **Sonnet 4.6** ✅ | ₹21.3k | ₹33.7k |
| Opus 4.8 | ₹35.5k | ₹56.1k |

### 4.3 Time (per 1,000, ~12 concurrent workers)
| Model | Code | PDF |
|---|---|---|
| Sonnet 4.6 / Sonnet 5 | ~1.5–2.0 h | ~1.5–2.2 h |
| Opus 4.8 | ~2.5–3.0 h | ~2.5–3.2 h |

_Overhead of median‑3 over single‑grade is only **~27%** (caching + score‑only). Adaptive‑N adds
~10% avg. Median‑of‑5 (high‑stakes) ≈ ×1.4 on the ensemble. Batch API halves any figure._

---

## 5. Weekly & monthly budget (conservative / safe‑side)

**Planning assumptions (deliberately padded for the pitch):**
- **5 gradings / week** (actual recent cadence ≈ 2/week)
- **500 submissions / grading** (actual avg ≈ 190, observed max 411)
- → **2,500 submissions / week** · **~10,825 / month** (5 × 4.33 wk × 500)
- Optimised median‑of‑3, code‑repo cost basis (PDF‑text ≈ −18%, PDF‑image ≈ +30%)

| Model | Per submission | **Weekly (2,500)** | **Monthly (~10,825)** |
|---|---|---|---|
| Haiku 4.5 ❌ (fails consistency) | ₹8.6 | ₹21,500 | ₹0.93 L |
| Sonnet 5 (fallback) | ₹17.3 | ₹43,250 | ₹1.87 L |
| **Sonnet 4.6 (recommended)** | ₹25.9 | **₹64,750** | **₹2.80 L** |
| Opus 4.8 (disputes only) | ₹43.1 | ₹1,07,750 | ₹4.67 L |

_These are upper‑bound numbers (real volume ≈ 40% of these assumptions today). PDF‑heavy weeks run
higher, code‑heavy weeks lower. Batch API (24 h latency) halves the figures if ever needed._

---

## 6. Recommendation

1. **Model: Sonnet 4.6** — decisively the most consistent (median‑of‑3 SD **0.21**, zero degenerate
   runs). Sonnet 5 is the cheaper fallback (SD 0.69, but has all‑zero runs the median must clean);
   Haiku 4.5 is **rejected on consistency**; Opus reserved for dispute re‑grades.
2. **Approach: optimised median‑of‑3** (adaptive to 5 on disagreement), score‑only ensemble +
   evidence once, prompt caching, degenerate‑run rejection, pinned model snapshot.
3. **Rubric: always atomise** the grader‑internal rubric (§2.2) — shrinks per‑run noise further.
4. **Budget to pitch (safe‑side, Sonnet 4.6): ~₹65k / week, ~₹2.8 L / month.** Cost‑optimised
   fallback (Sonnet 5): ~₹43k / week, ~₹1.87 L / month. Real current volume is ~40% of these.
5. **Not achievable:** the ~₹2k target — each submission must be read once, so a hosted API has a
   hard floor. Only a local model (your Qwen) reaches that, and Haiku (the hosted "cheap" option)
   fails consistency.

**Next step:** atomise Q2/Q4 and re‑run the harness on Sonnet 4.6 to confirm SD tightens even further
(it's already 0.21) — then wire median‑of‑3 on Sonnet 4.6 into the engine as the score of record.
