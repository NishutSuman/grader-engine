"""
Model evaluation harness — decide a grading model by MEASUREMENT, not vibes.

Grades a FIXED sample of already-graded submissions K times each, per part, on one or
more models, and reports what actually matters for a grading engine:
  - CONSISTENCY  : run-to-run SD per submission (single-run + median-of-3), degenerate rate
  - AGREEMENT    : mean |score - Sonnet-4.6 reference| (are the marks the same?)
  - FEEDBACK     : captured for review (+ optional LLM-judge, added separately)
  - PRICE        : tokens x per-model $/1M  -> Rs/submission
  - TIME         : wall-clock per call

Provider-agnostic:
  - "anthropic"      : Claude via the Anthropic SDK (this IS our production path).
  - "openai_compat"  : MiniMax M3 / GLM 5.2 etc. via an OpenAI-compatible base_url.

Evaluation ONLY. Never writes grades to the DB.
"""
from __future__ import annotations

import json
import os
import statistics as st
import time
from pathlib import Path

from grader import grade as G
from app.db import repo
from app.db.session import eval_data_dir, get_session

USD_INR = 100.0


def _part_blocks(dl_root, code, ev_ns, part):
    pdir = dl_root / code / part.key
    imgs, body = G.selective_blocks(pdir) if pdir.exists() else ([], "")
    if not body:
        return None
    prompt = G.build_prompt(ev_ns, [part], code, ps_override=part.problem_statement)
    return imgs + [{"type": "text", "text": body[:150000]}, {"type": "text", "text": prompt}]


def _anthropic_call(client, model, part, blocks):
    tool = G.build_tool([part])
    t0 = time.time()
    msg = client.with_options(timeout=300.0).messages.create(
        model=model, max_tokens=16000, tools=[tool],
        tool_choice={"type": "tool", "name": tool["name"]},
        messages=[{"role": "user", "content": blocks}])
    dt = time.time() - t0
    tu = next(x for x in msg.content if getattr(x, "type", None) == "tool_use")
    sec = G.parse_tool(tu.input, [part])["sections"][part.key]
    return {"score": sec["score"], "feedback": G.render_feedback(sec),
            "in_tok": msg.usage.input_tokens, "out_tok": msg.usage.output_tokens, "sec": dt}


def _openai_compat_call(cfg, part, blocks):
    """MiniMax M3 / GLM 5.2 via OpenAI-compatible chat.completions + JSON mode.
    (Image blocks are dropped here — fine for code-repo evals; PDF-image evals need
    a multimodal variant.)"""
    from openai import OpenAI
    client = OpenAI(base_url=cfg["base_url"], api_key=os.environ[cfg["key_env"]],
                    timeout=120.0, max_retries=5)   # 120s per attempt (kills hangs) + backoff on 429
    text = "\n\n".join(b["text"] for b in blocks if b.get("type") == "text")
    schema = (f'\n\nReturn ONLY a JSON object: {{"score": <number 0..{part.max_marks}>, '
              f'"feedback": "<strengths, then areas for improvement>", '
              f'"deductions": [{{"criterion": "...", "reason": "...", "marks_lost": <number>}}]}}. '
              f'No prose outside the JSON.')
    # GLM/MiniMax-style reasoning models burn tokens on hidden thinking; disabling
    # it is a key cost lever to test (≈5× fewer output tokens, ≈3× faster) — but
    # the payload shape that actually turns it off is NOT the same across
    # backends (confirmed live: OpenRouter ignores GLM's {"thinking": {"type":
    # "disabled"}} entirely and just burns the whole max_tokens budget on hidden
    # reasoning, returning empty content). Pass the exact shape via cfg
    # ("reasoning_off": {...}) instead of a single hardcoded one - see
    # grade.OPENAI_COMPAT_PRESETS for the two confirmed-working shapes.
    kwargs = {"extra_body": cfg["reasoning_off"]} if cfg.get("reasoning_off") else {}
    # json_object forces pure JSON from token 1, which SUPPRESSES a reasoning model's
    # think step. Skip it for reasoning models (json_mode=False) and extract via regex.
    if cfg.get("json_mode", True):
        kwargs["response_format"] = {"type": "json_object"}
    t0 = time.time()
    resp = client.chat.completions.create(
        model=cfg["model"], max_tokens=cfg.get("max_tokens", 4000), temperature=0,
        messages=[{"role": "user", "content": text + schema}], **kwargs)
    dt = time.time() - t0
    msg = resp.choices[0].message
    raw = (msg.content or "").strip()
    if not raw:                                # reasoning models sometimes leave content empty
        raw = (getattr(msg, "reasoning_content", "") or "").strip()
    if "</think>" in raw:                      # reasoning models: the JSON follows the think block
        raw = raw.split("</think>")[-1]
    import re as _re
    mobj = _re.search(r"\{.*\}", raw, _re.S)    # tolerate prose/``` fences around the JSON
    data = json.loads(mobj.group(0) if mobj else raw)
    return {"score": float(data.get("score", 0) or 0), "feedback": data.get("feedback", ""),
            "in_tok": resp.usage.prompt_tokens, "out_tok": resp.usage.completion_tokens, "sec": dt}


def run(models: list[dict], sample_codes: list[str], runs: int = 5,
        slug: str = "bitsom-ba-2512-79650", parts: list[str] | None = None, log=print,
        workers: int = 8) -> dict:
    """models: [{name, provider, model, price_in, price_out, [base_url], [key_env]}].
    Calls fan out across `workers` threads (grading calls are I/O-bound)."""
    import anthropic
    from grader.rubric_ai import _ensure_env
    _ensure_env()
    s = get_session()
    ev = repo.get_eval(s, slug)
    allparts = repo.parts(s, ev.id)
    pobjs = [p for p in G._plain_parts(allparts) if (not parts or p.key in parts)]
    ev_ns = G._plain_ev(ev)
    dl_root = eval_data_dir(ev.slug) / "downloads"

    ref = {}
    for code in sample_codes:
        stu = repo.get_student(s, ev.id, code)
        for g in repo.grades_for(s, stu.id):
            ref[(code, g.part_key)] = g.score

    # Only build the Anthropic client if a Claude model is actually being tested —
    # the key is intentionally absent when comparing local/other providers.
    need_anthropic = any(m["provider"] == "anthropic" for m in models)
    aclient = anthropic.Anthropic() if need_anthropic else None
    from concurrent.futures import ThreadPoolExecutor, as_completed
    out = {}
    for m in models:
        recs = []
        log(f"\n=== {m['name']} ({m['provider']}) ===")
        # build each (code, part) digest once, then fan the runs out concurrently
        tasks = []
        for code in sample_codes:
            for p in pobjs:
                blocks = _part_blocks(dl_root, code, ev_ns, p)
                if not blocks:
                    continue
                tasks += [(code, p, blocks, k) for k in range(runs)]

        def _one(code, p, blocks, k):
            try:
                r = (_anthropic_call(aclient, m["model"], p, blocks)
                     if m["provider"] == "anthropic" else _openai_compat_call(m, p, blocks))
                return {"code": code, "part": p.key, "run": k, "ref": ref.get((code, p.key)), **r}
            except Exception as e:
                return {"code": code, "part": p.key, "run": k, "error": f"{type(e).__name__}: {e}"[:140]}

        done = 0
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = [ex.submit(_one, *t) for t in tasks]
            for fut in as_completed(futs):
                recs.append(fut.result())
                done += 1
                if done % 5 == 0 or done == len(tasks):
                    log(f"  {done}/{len(tasks)} calls done")
        out[m["name"]] = {"cfg": m, "recs": recs}
    return {"models": out, "ref": ref, "runs": runs, "n_parts": len(pobjs)}


def report(res: dict, log=print):
    for name, d in res["models"].items():
        recs = [r for r in d["recs"] if "error" in r]
        good = [r for r in d["recs"] if "error" not in r]
        cfg = d["cfg"]
        # consistency: SD across runs per (code,part)
        from collections import defaultdict
        by_cp = defaultdict(list)
        for r in good:
            by_cp[(r["code"], r["part"])].append(r)
        sds, deltas, med3_sds = [], [], []
        for (code, part), rr in by_cp.items():
            scores = [x["score"] for x in rr]
            if len(scores) > 1:
                sds.append(st.pstdev(scores))
            refv = rr[0].get("ref")
            if refv is not None:
                deltas.append(abs(st.median(scores) - refv))
            # median-of-3 stability: SD of disjoint median-of-3 groups
            if len(scores) >= 6:
                med3 = [st.median(scores[i:i+3]) for i in range(0, len(scores) - len(scores) % 3, 3)]
                if len(med3) > 1:
                    med3_sds.append(st.pstdev(med3))
        in_tok = st.mean([r["in_tok"] for r in good]) if good else 0
        out_tok = st.mean([r["out_tok"] for r in good]) if good else 0
        rs = (in_tok / 1e6 * cfg["price_in"] + out_tok / 1e6 * cfg["price_out"]) * USD_INR
        sec = st.mean([r["sec"] for r in good]) if good else 0
        log(f"\n### {name}")
        log(f"  runs OK: {len(good)} · errors: {len(recs)}")
        log(f"  single-run SD (per submission-part): mean {round(st.mean(sds),2) if sds else '-'} "
            f"(max {round(max(sds),2) if sds else '-'})")
        log(f"  median-of-3 SD: {round(st.mean(med3_sds),2) if med3_sds else 'n/a (need >=6 runs)'}")
        log(f"  agreement vs Sonnet-4.6 ref: mean |delta| {round(st.mean(deltas),2) if deltas else '-'} marks/part")
        log(f"  avg tokens: {round(in_tok)} in / {round(out_tok)} out · ~Rs {round(rs,2)}/part-call · {round(sec,1)}s/call")
        if recs:
            log(f"  sample error: {recs[0].get('error')}")
