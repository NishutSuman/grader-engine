# Grading Cost — Per Submission, vs Manual, and Our Evals

_Companion to `grading-cost-and-consistency.md`. All costs at **₹100 / USD**. Code‑repo tokens are
**measured** (~35k in / 6.6k out); PDF tokens are estimated. "Median‑3" = the recommended
consistent method (optimised: caching + score‑only ensemble)._

---

## 1. Cost per submission — by category & model

| Submission type | Model | **Single grade (1×)** | **Median‑of‑3 (recommended)** |
|---|---|---|---|
| **Code repo** (GitHub) | Haiku 4.5 ❌ | ₹6.8 | ₹8.6 |
| | Sonnet 5 | ₹13.6 | ₹17.3 |
| | **Sonnet 4.6** ✅ | ₹20.4 | **₹25.9** |
| | Opus 4.8 | ₹34.0 | ₹43.1 |
| **PDF — text/typed** | Sonnet 5 | ₹11.0 | ₹14.2 |
| | **Sonnet 4.6** ✅ | ₹16.5 | **₹21.3** |
| | Opus 4.8 | ₹27.5 | ₹35.5 |
| **PDF — image/wireframe‑heavy** | Sonnet 5 | ₹17.0 | ₹22.5 |
| | **Sonnet 4.6** ✅ | ₹25.5 | **₹33.7** |
| | Opus 4.8 | ₹42.5 | ₹56.1 |

_❌ Haiku 4.5 is priced for reference only — it failed the consistency test (median‑3 SD 6.3) and is
not used. ✅ Sonnet 4.6 is the recommended model (median‑3 SD 0.21, no degenerate runs)._

---

## 2. AI vs manual grading (manual = ₹100 / submission)

| Method (per submission) | Cost | vs Manual (₹100) |
|---|---|---|
| Manual (human grader) | ₹100 | — |
| AI — Sonnet 4.6, **median‑of‑3** (code) | ₹25.9 | **3.9× cheaper** (‑74%) |
| AI — Sonnet 4.6, median‑of‑3 (PDF image) | ₹33.7 | 3.0× cheaper (‑66%) |
| AI — Sonnet 5, median‑of‑3 (code) | ₹17.3 | 5.8× cheaper (‑83%) |
| AI — single grade (code, Sonnet 5) | ₹13.6 | 7.4× cheaper (‑86%) |

Even the **most rigorous, dispute‑proof** setting (Sonnet 4.6 × median‑of‑3) is **~3–4× cheaper than
manual** — and unlike manual grading it is consistent, evidence‑cited, and instant.

---

## 3. What we've spent — last 4 evals + 1 pending

Counts are **actual** (graded students in the DB). The 4 completed evals were graded **single (1×)**
with Sonnet 5 / imported runs; **IITP‑AIML (pending)** is projected with the new **Sonnet 4.6 ×
median‑of‑3** method.

| Eval | Graded | Type | Method | ₹/sub | **AI cost** | Manual (₹100) | **Saved** |
|---|---|---|---|---|---|---|---|
| IITP‑PM 2510 (scripts) | 88 | PDF · 6‑PDF folder | Sonnet 5, single (vision) | ~₹20 | ₹1,760 | ₹8,800 | ₹7,040 |
| IITR‑PM 2510 (scripts) | 195 | PDF · PM capstone | Sonnet 5, single (vision) | ~₹20 | ₹3,900 | ₹19,500 | ₹15,600 |
| BITSoM Prompt‑Eng (scripts) | 411 | Code · GitHub | Sonnet 5, single | ~₹13.6 | ₹5,590 | ₹41,100 | ₹35,510 |
| IITMandi‑BA 2504 (**app**) | 20 | Code · GitHub | Sonnet 5, single | ~₹13.6 | ₹272 | ₹2,000 | ₹1,728 |
| **Subtotal — done** | **714** | | | | **₹11,522** | **₹71,400** | **₹59,878** |
| IITP‑AIML 2506 (**pending**) | 182 | Code · GitHub | **Sonnet 4.6, median‑3** | ₹25.9 | ₹4,714 | ₹18,200 | ₹13,486 |
| **TOTAL — 5 evals** | **896** | | | | **₹16,236** | **₹89,600** | **₹73,364** |

> Across **896 submissions**, AI grading cost **~₹16,200** vs **~₹89,600** manual — a **~82% saving
> (~₹73,400)**, at the current mix of single‑graded (past) + median‑3 (IITP‑AIML).


_(PDF evals @ ₹33.7, code evals @ ₹25.9.)_ Even at our **most expensive, most consistent** setting,
we grade all 896 submissions for **~₹25k vs ₹90k manual** — and every score is reproducible and
evidence‑backed, which manual grading at this scale cannot guarantee.

---

## 4. Bottom line for the team

- **Per‑submission** (recommended Sonnet 4.6 × median‑of‑3): **₹25.9 code / ₹33.7 PDF‑image** — vs
  **₹100 manual** → **3–4× cheaper**, and consistent + auditable.
- **Spent so far** (896 submissions, 5 evals): **~₹16k AI vs ~₹90k manual (~82% saved)**.
- **Upgrade cost of full consistency**: doing *everything* the rigorous median‑3 way would be
  ~₹25k total — still **~72% below manual**, and it removes the dispute risk that single‑grading carries.
