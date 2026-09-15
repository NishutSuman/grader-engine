"""Central cost + method-recommendation model for the grading engine.

Single source of truth for:
  • model pricing (₹100/USD) and per-method cost,
  • the two methods — single grade vs the recommended **median-of-3** (optimised
    with prompt caching + score-only ensemble runs),
  • measured consistency priors from the harness (`grader/consistency.py`),
  • the recommendation that treats CONSISTENCY as a hard gate, then minimises
    cost by submission type + weightage/stakes.

Pure functions, no I/O — trivially unit-testable and reused by the sample-grading
preview and the grading engine.
"""
from __future__ import annotations

USD_INR = 100

# $/1M tokens: (input, output, cache_write, cache_read). Sonnet 5 = intro pricing.
PRICES = {
    "claude-haiku-4-5":  (1.0, 5.0, 1.25, 0.10),
    "claude-sonnet-5":   (2.0, 10.0, 2.50, 0.20),
    "claude-sonnet-4-6": (3.0, 15.0, 3.75, 0.30),
    "claude-opus-4-8":   (5.0, 25.0, 6.25, 0.50),
}
LABELS = {"claude-haiku-4-5": "Haiku 4.5", "claude-sonnet-5": "Sonnet 5",
          "claude-sonnet-4-6": "Sonnet 4.6", "claude-opus-4-8": "Opus 4.8"}

# Harness result (5 subs × 9 runs, /100). med3_sd = SD of the median-of-3 (the
# released statistic). Consistency is a HARD gate — see MAX_MED3_SD.
CONSISTENCY = {
    "claude-sonnet-4-6": {"single_sd": 0.62, "med3_sd": 0.21, "degenerate": False, "grade": "excellent"},
    "claude-sonnet-5":   {"single_sd": 4.9,  "med3_sd": 0.69, "degenerate": True,  "grade": "good"},
    "claude-haiku-4-5":  {"single_sd": 12.1, "med3_sd": 6.32, "degenerate": True,  "grade": "unusable"},
    "claude-opus-4-8":   {"single_sd": None, "med3_sd": None, "degenerate": None,  "grade": "untested"},
}
MAX_MED3_SD = 1.5             # median-3 SD above this ⇒ not recommendable
DEFAULT_MODEL = "claude-sonnet-4-6"   # most consistent, no degenerate runs
SCORE_ONLY_OUT = 250         # output tokens for an ensemble score-only run
ENSEMBLE_N = {"single": 1, "median3": 3, "median5": 5}


# ── cost ─────────────────────────────────────────────────────────────────────
def method_cost(avg_in: float, avg_out: float, model: str,
                method: str = "median3", batch: bool = False) -> float:
    """USD per student. median-N is optimised: input = 1 cache-write + (N-1)
    cache-reads (identical input reused); output = 1 full grade + (N-1) cheap
    score-only runs. Batch API halves everything."""
    pin, pout, cw, cr = PRICES[model]
    n = ENSEMBLE_N[method]
    if n == 1:
        cost = (avg_in * pin + avg_out * pout) / 1e6
    else:
        inp = avg_in * (cw + (n - 1) * cr) / 1e6
        out = (avg_out + (n - 1) * SCORE_ONLY_OUT) * pout / 1e6
        cost = inp + out
    return cost * (0.5 if batch else 1.0)


def method_time_sec(avg_sec: float, n_students: int, method: str = "median3",
                    workers: int = 12) -> float:
    """Wall-clock seconds for the full cohort. A score-only run is ~0.3× a full
    grade (same input prefill, tiny output). Parallel across `workers`."""
    n = ENSEMBLE_N[method]
    per_student = avg_sec + (n - 1) * avg_sec * 0.3
    return per_student * n_students / max(1, workers)


def inr(usd: float) -> float:
    return usd * USD_INR


# ── recommendation ───────────────────────────────────────────────────────────
def _passes(model: str) -> bool:
    c = CONSISTENCY[model]
    if c["grade"] == "unusable":
        return False
    if c["med3_sd"] is not None and c["med3_sd"] > MAX_MED3_SD:
        return False
    return True


def options(avg_in: float, avg_out: float, n_students: int,
            method: str = "median3", batch: bool = False) -> list[dict]:
    """One row per model for the comparison table (cost, consistency, verdict)."""
    out = []
    for m in PRICES:
        c = CONSISTENCY[m]
        per = method_cost(avg_in, avg_out, m, method, batch)
        out.append({
            "model": m, "label": LABELS[m],
            "per_student_inr": round(inr(per), 2),
            "full_usd": round(per * n_students, 2),
            "full_inr": round(inr(per) * n_students),
            "med3_sd": c["med3_sd"], "degenerate": c["degenerate"],
            "grade": c["grade"], "recommendable": _passes(m),
            "time_min": round(method_time_sec(0, 0) or 0),  # filled by caller if avg_sec known
        })
    return out


def method_compare(avg_in: float, avg_out: float, avg_sec: float,
                   n_students: int, model: str) -> list[dict]:
    """single vs median-3 vs median-5 for one model — cost, time, consistency note."""
    notes = {"single": "cheapest, but a single run is unsafe — one bad/degenerate run gets released",
             "median3": "recommended — rejects outliers; released score is stable",
             "median5": "high-stakes — tightest, tolerates 2 bad runs"}
    rows = []
    for meth in ("single", "median3", "median5"):
        per = method_cost(avg_in, avg_out, model, meth)
        rows.append({"method": meth, "label": {"single": "Single (1×)",
                     "median3": "Median-of-3", "median5": "Median-of-5"}[meth],
                     "per_student_inr": round(inr(per), 2),
                     "full_inr": round(inr(per) * n_students),
                     "time_min": round(method_time_sec(avg_sec, n_students, meth) / 60, 1),
                     "note": notes[meth], "recommended": meth == "median3"})
    return rows


def recommend(avg_in: float, avg_out: float, avg_sec: float, n_students: int,
              tier: dict, sub_type: str = "code",
              measured_med3_sd: float | None = None) -> dict:
    """Balance consistency (hard gate) → cost → stakes.

    - Default is the most-consistent model (Sonnet 4.6).
    - For LOW-stakes evals, if a cheaper *consistent* model saves materially, it
      is offered as the recommended cost-saver.
    - HIGH-stakes always take the most-consistent, degenerate-free model.
    - Models that fail consistency (Haiku) are shown but never recommended.
    """
    t = tier.get("tier", "moderate")
    opts = options(avg_in, avg_out, n_students, "median3")
    for o in opts:                                   # attach real time estimate
        o["time_min"] = round(method_time_sec(avg_sec, n_students, "median3") / 60, 1)
    by_model = {o["model"]: o for o in opts}

    default = by_model[DEFAULT_MODEL]
    cheaper = by_model["claude-sonnet-5"]            # cheapest consistent alternative
    # a cost-saver is worth surfacing only if it passes consistency AND saves ≥15%
    saving = (default["full_inr"] - cheaper["full_inr"])
    saving_pct = round(saving / default["full_inr"] * 100) if default["full_inr"] else 0
    saver_ok = cheaper["recommendable"] and saving_pct >= 15

    if t == "heavy":
        rec, why = DEFAULT_MODEL, (
            "High-stakes eval (gates certification/placement) — use the most "
            "consistent, degenerate-free model (Sonnet 4.6). No cost cutting here.")
        saver = None
    elif t == "light" and saver_ok:
        rec, why = "claude-sonnet-5", (
            f"Low-stakes eval — Sonnet 5 (median-of-3) is consistent enough "
            f"(SD {cheaper['med3_sd']}) and saves ~{saving_pct}% (₹{saving:,}). "
            f"Sonnet 4.6 remains the safest if you prefer zero degenerate-run risk.")
        saver = None                                 # sonnet-5 IS the recommendation here
    else:  # moderate (or light with negligible saving)
        rec, why = DEFAULT_MODEL, (
            "Sonnet 4.6 (median-of-3) — the most consistent option (SD "
            f"{default['med3_sd']}, no degenerate runs).")
        saver = ({"model": "claude-sonnet-5", "label": "Sonnet 5",
                  "saving_inr": saving, "saving_pct": saving_pct,
                  "note": f"Sonnet 5 (median-of-3) would save ~₹{saving:,} (~{saving_pct}%) "
                          f"at SD {cheaper['med3_sd']} — acceptable if budget-constrained."}
                 if saver_ok else None)

    return {
        "n": n_students, "sub_type": sub_type, "tier": tier, "method": "median3",
        "options": opts, "by_model": by_model,
        "method_compare": method_compare(avg_in, avg_out, avg_sec, n_students, rec),
        "recommend": rec, "recommend_label": LABELS[rec], "why": why,
        "cost_saver": saver,
        "measured_med3_sd": measured_med3_sd,          # this eval's own sampled consistency
        "recommended_full_inr": by_model[rec]["full_inr"],
        "recommended_time_min": by_model[rec]["time_min"],
    }
