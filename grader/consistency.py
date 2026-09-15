"""Grading-consistency harness — quantify how much the grader's scores wobble
run-to-run on IDENTICAL input.

Why: LLM grading is non-deterministic. Before we make the engine "more consistent"
(temperature 0, ensemble median, rubric atomisation, …) we need a baseline number
to measure each change against. This grades a sample of already-downloaded
submissions N times each and reports per-section + total variance.

READ-ONLY: it never writes grades to the DB — it only calls the model and does math.

Reuses the REAL grading path (grade.build_tool / _blocks_for / build_prompt →
dataset brief + rubric + submission), so what we measure is the actual engine —
optionally overriding `temperature`/`model` to compare configs.
"""
from __future__ import annotations

import statistics as stt
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Optional

from app.db import repo
from app.db.session import eval_data_dir, get_session
from grader.grade import _blocks_for, build_tool, parse_tool


def _client():
    from grader.rubric_ai import _ensure_env
    _ensure_env()
    import anthropic
    return anthropic.Anthropic()


def _grade_once(client, mdl, tool, parts, blocks, max_tokens, thinking_off=False):
    # NOTE: `temperature` is deprecated on sonnet-5 / the 4.6+ family — can't be set.
    # The only model-level knob is thinking on/off.
    kw = dict(model=mdl, max_tokens=max_tokens, tools=[tool],
              tool_choice={"type": "tool", "name": "submit_grades"},
              messages=[{"role": "user", "content": blocks}])
    if thinking_off:
        kw["thinking"] = {"type": "disabled"}
    msg = client.with_options(timeout=300.0).messages.create(**kw)
    tu = next(x for x in msg.content if getattr(x, "type", None) == "tool_use")
    return parse_tool(tu.input, parts)


def _sample_students(studs, n):
    """Evenly spread across the (code-sorted) downloaded roster — a representative
    slice rather than the first N."""
    ok = [st for st in sorted(studs, key=lambda x: x.student_code)
          if st.download_status == "ok"]
    if len(ok) <= n:
        return ok
    step = len(ok) / n
    return [ok[int(i * step)] for i in range(n)]


def measure(eval_id: int, sample: int = 6, repeats: int = 5,
            thinking_off: bool = False, model: Optional[str] = None,
            max_tokens: int = 16000, workers: int = 8,
            progress: Callable[[str], None] = print) -> dict:
    s = get_session()
    ev = repo.get_eval_by_id(s, eval_id)
    parts = repo.parts(s, eval_id)
    mdl = model or ev.grader_model
    tool = build_tool(parts)
    dl_root = eval_data_dir(ev.slug) / "downloads"

    jobs = []                                        # (code, blocks) — input is fixed per student
    for st in _sample_students(repo.students(s, eval_id), sample):
        blocks = _blocks_for(dl_root / st.student_code, ev, parts, st.student_code,
                             st.submission_raw if st.submission_type == "inline" else "")
        if blocks:
            jobs.append((st.student_code, blocks))
    tag = "thinking-off" if thinking_off else "thinking-default"
    tasks = [(code, blocks) for code, blocks in jobs for _ in range(repeats)]
    progress(f"[{tag}] grading {len(jobs)} students × {repeats} = {len(tasks)} calls ({mdl})…")

    client = _client()
    results: dict[str, list] = {code: [] for code, _ in jobs}
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_grade_once, client, mdl, tool, parts, blocks, max_tokens, thinking_off): code
                for code, blocks in tasks}
        for f in as_completed(futs):
            code = futs[f]
            try:
                results[code].append(f.result())
            except Exception as e:
                progress(f"  {code}: error {str(e)[:70]}")
            done += 1
            if done % 5 == 0 or done == len(tasks):
                progress(f"  {done}/{len(tasks)} calls done")
    return _stats(ev, parts, results, tag, mdl, repeats)


def _med3_sd(totals: list[float], k: int = 3):
    """SD of the median-of-k across disjoint groups — how stable an ensemble-median
    score-of-record would be vs a single run. Needs >= 2 groups (>= 2k runs)."""
    groups = [totals[i:i + k] for i in range(0, len(totals) - len(totals) % k, k)]
    if len(groups) < 2:
        return None, []
    meds = [stt.median(g) for g in groups]
    return round(stt.stdev(meds), 2), [round(m, 1) for m in meds]


def _stats(ev, parts, results, tag, mdl, repeats) -> dict:
    per_student, tot_sds, spreads, med_sds = [], [], [], []
    sec_sds = {p.key: [] for p in parts}
    for code, runs in results.items():
        if len(runs) < 2:
            continue
        totals = [r["total"] for r in runs]
        sd, spread = stt.stdev(totals), max(totals) - min(totals)
        tot_sds.append(sd); spreads.append(spread)
        msd, meds = _med3_sd(totals)
        if msd is not None:
            med_sds.append(msd)
        for p in parts:
            vals = [r["sections"][p.key]["score"] for r in runs]
            sec_sds[p.key].append(stt.stdev(vals) if len(vals) > 1 else 0.0)
        per_student.append({"code": code, "totals": totals,
                            "mean": round(stt.mean(totals), 1), "median": round(stt.median(totals), 1),
                            "sd": round(sd, 2), "spread": round(spread, 2),
                            "med3_sd": msd, "med3": meds})
    per_student.sort(key=lambda x: -x["spread"])
    agg = {"n": len(per_student), "repeats": repeats,
           "mean_total_sd": round(stt.mean(tot_sds), 2) if tot_sds else 0,
           "median_spread": round(stt.median(spreads), 2) if spreads else 0,
           "worst_spread": round(max(spreads), 2) if spreads else 0,
           "mean_med3_sd": round(stt.mean(med_sds), 2) if med_sds else None,
           "sec_sd": {p.key: round(stt.mean(sec_sds[p.key]), 2) if sec_sds[p.key] else 0 for p in parts}}
    return {"eval": ev.slug, "model": mdl, "tag": tag, "total_marks": ev.total_marks,
            "normalize_to": ev.normalize_to, "per_student": per_student, "agg": agg,
            "parts": [(p.key, p.max_marks) for p in parts]}


def print_report(rep: dict) -> None:
    a = rep["agg"]
    print(f"\n{'='*66}\nCONSISTENCY — {rep['eval']} · {rep['model']} · {rep['tag']} · "
          f"/{rep['total_marks']}\n{a['n']} students × {a['repeats']} runs each\n{'='*66}")
    print(f"{'student':22}{'runs (total /'+str(rep['total_marks'])+')':30}{'mean':>7}{'sd':>6}{'spread':>8}")
    for r in rep["per_student"]:
        runs = "[" + ",".join(f"{t:g}" for t in r["totals"]) + "]"
        print(f"{r['code']:22}{runs:30}{r['mean']:>7}{r['sd']:>6}{r['spread']:>8}")
    print(f"\nAGGREGATE  single-run mean SD: {a['mean_total_sd']}  ·  median spread: "
          f"{a['median_spread']}  ·  worst spread: {a['worst_spread']}   (/{rep['total_marks']})")
    if a.get("mean_med3_sd") is not None:
        print(f"           median-of-3 mean SD: {a['mean_med3_sd']}   "
              f"(ensemble score-of-record stability — lower is better)")
    print("per-section mean SD:  " + "  ·  ".join(
        f"{k.split('_')[0]}(/{int(mx)}): {a['sec_sd'][k]}" for k, mx in rep["parts"]))
    print("=" * 66)
