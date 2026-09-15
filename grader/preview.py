"""Sample grading + method recommendation — the adaptive engine's front half.

Grades a few RANDOM downloaded submissions **N times each** (default 3) to measure,
for THIS eval, the real per-student token usage, timing, and run-to-run
consistency. Feeds `grader.costing` to project cost + time across models and
methods (single vs median-of-3) and recommend the best option — consistency-gated,
then cost-optimised by weightage. The full comparison + recommendation is saved
onto the eval (`config_json['preview']`) so the UI can render it and the grading
run can adopt it.
"""
from __future__ import annotations

import random
import statistics as stt
import time
from typing import Callable, Optional

from app.db import repo
from app.db.session import eval_data_dir, get_session
from grader import costing
from grader import grade as G
from grader.costing import DEFAULT_MODEL, LABELS
from grader.rubric_ai import weightage_tier

_CODE_TYPES = {"github", "gdoc", "inline", "url"}


def _sub_type(students) -> str:
    """Dominant submission format for display — 'code' vs 'pdf' (drive/s3 folders)."""
    subm = [s for s in students if s.submission_type != "empty"]
    if not subm:
        return "code"
    code = sum(1 for s in subm if s.submission_type in _CODE_TYPES)
    return "code" if code >= len(subm) / 2 else "pdf"


def _gradeable(s, ev, parts) -> list:
    dl_root = eval_data_dir(ev.slug) / "downloads"
    out = []
    for st in repo.students_to_grade(s, ev.id):          # download_status == ok
        inline = st.submission_raw if st.submission_type == "inline" else ""
        blocks = G._blocks_for(dl_root / st.student_code, ev, parts, st.student_code, inline)
        if blocks:
            out.append((st, blocks))
    return out


SAMPLE_THRESHOLD = 50          # >50 gradeable submissions → sample 3, else 1


def _measured_call(client, model, tool, blocks):
    """Like grade._grade_call's request: 5-min timeout + shrink-on-413 so one
    unusually large submission can't hang or 413 the whole sample-grading run."""
    cur = blocks
    while True:
        try:
            return client.with_options(timeout=300.0).messages.create(
                model=model, max_tokens=16000, tools=[tool],
                tool_choice={"type": "tool", "name": "submit_grades"},
                messages=[{"role": "user", "content": cur}])
        except Exception as e:
            if not G._is_size_error(e):
                raise
            smaller = G._shrink_blocks(cur)
            if smaller is None:
                raise
            cur = smaller


def preview_grade(eval_id: int, sample_n: Optional[int] = None, repeats: int = 3,
                  model: str = DEFAULT_MODEL,
                  progress: Optional[Callable[[str], None]] = None) -> dict:
    """Grade sample_n random submissions `repeats`× each; measure tokens, time,
    and this-eval's run-to-run consistency (SD of the totals per submission).
    sample_n defaults to 3 when there are more than 50 gradeable submissions,
    otherwise 1 (a small cohort doesn't need three samples to project cost)."""
    import anthropic
    log = progress or (lambda m: None)
    s = get_session()
    ev = repo.get_eval_by_id(s, eval_id)
    parts = repo.parts(s, eval_id)
    tool = G.build_tool(parts)
    cand = _gradeable(s, ev, parts)
    if not cand:
        raise ValueError("no gradeable submissions with content — run download first")
    sub_type = _sub_type(repo.students(s, ev.id))
    n_target = sample_n if sample_n is not None else (3 if len(cand) > SAMPLE_THRESHOLD else 1)
    sample = random.sample(cand, min(n_target, len(cand)))
    from grader.rubric_ai import _ensure_env
    _ensure_env()
    client = anthropic.Anthropic()
    log(f"5|sample-grading {len(sample)} students ×{repeats} with {LABELS.get(model, model)} "
        f"(measuring cost + consistency)…")

    samples, all_in, all_out, all_sec, all_sd = [], [], [], [], []
    total_calls = len(sample) * repeats
    done = 0
    for st, blocks in sample:
        scores, ins, outs, secs = [], [], [], []
        for _ in range(repeats):
            t0 = time.time()
            msg = _measured_call(client, model, tool, blocks)
            dt = time.time() - t0
            tu = next(x for x in msg.content if getattr(x, "type", None) == "tool_use")
            r = G.parse_tool(tu.input, parts)
            scores.append(r["total"]); ins.append(msg.usage.input_tokens)
            outs.append(msg.usage.output_tokens); secs.append(dt)
            done += 1
            log(f"{int(5 + done / total_calls * 90)}|graded {done}/{total_calls} sample runs")
        sd = stt.stdev(scores) if len(scores) > 1 else 0.0
        samples.append({"code": st.student_code, "scores": scores,
                        "median": stt.median(scores), "single_sd": round(sd, 2),
                        "max": ev.total_marks, "in_tok": round(stt.mean(ins)),
                        "out_tok": round(stt.mean(outs)), "sec": round(stt.mean(secs), 1)})
        all_in.append(stt.mean(ins)); all_out.append(stt.mean(outs))
        all_sec.append(stt.mean(secs)); all_sd.append(sd)
        log(f"  {st.student_code}: median {stt.median(scores)}/{ev.total_marks} "
            f"(runs {scores}) · SD {sd:.1f} · {round(stt.mean(ins))} in / {round(stt.mean(outs))} out")
    n = len(samples)
    return {"model": model, "model_label": LABELS.get(model, model), "n_sample": n,
            "repeats": repeats, "sub_type": sub_type,
            "avg_in": round(stt.mean(all_in)), "avg_out": round(stt.mean(all_out)),
            "avg_sec": round(stt.mean(all_sec), 1),
            "measured_single_sd": round(stt.mean(all_sd), 2),
            "n_gradeable": len(cand), "samples": samples}


def preview_and_recommend(eval_id: int, sample_n: Optional[int] = None, repeats: int = 3,
                          model: str = DEFAULT_MODEL,
                          progress: Optional[Callable[[str], None]] = None) -> dict:
    """Sample-grade → recommend (consistency-gated, cost-optimised) → persist."""
    stats = preview_grade(eval_id, sample_n=sample_n, repeats=repeats, model=model, progress=progress)
    s = get_session()
    ev = repo.get_eval_by_id(s, eval_id)
    tier = weightage_tier((ev.config_json or {}).get("pending", {}).get("weightage", ""))
    rec = costing.recommend(stats["avg_in"], stats["avg_out"], stats["avg_sec"],
                            stats["n_gradeable"], tier, stats["sub_type"],
                            measured_med3_sd=stats["measured_single_sd"])
    from datetime import datetime, timezone
    cfg = dict(ev.config_json or {})
    cfg["preview"] = {"stats": stats, "rec": rec,
                      "analyzed_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    ev.config_json = cfg
    ev.grader_model = rec["recommend"]                   # engine adopts the recommendation
    s.commit()
    repo.audit(s, "sample_grading", eval_id=eval_id,
               after={"sampled": stats["n_sample"], "repeats": repeats,
                      "recommend": rec["recommend"], "full_inr": rec["recommended_full_inr"],
                      "measured_single_sd": stats["measured_single_sd"]})
    s.commit()
    return {"sampled": stats["n_sample"], "recommend": rec["recommend_label"],
            "full_inr": rec["recommended_full_inr"]}
