"""Ticket resolution engine — read a student's grade challenge, diagnose it against
hard evidence, regrade if (and only if) warranted, and draft a reply. Language and
judgment are the LLM's; every score-changing decision is gated by deterministic
code (no-decrease, defect diagnostics, median-of-N).

Phase A: `analyze()` proposes (no writes, no LMS post). `apply()` persists an
approved proposal (still no LMS post — the human submits the drafted reply).
"""
from __future__ import annotations

import json
import secrets
from pathlib import Path
from typing import Callable, Optional

from app.db import repo
from app.db.session import eval_data_dir, get_session
from grader import grade as G
from grader import report, storage

_MODEL = "claude-sonnet-4-6"


def _client():
    from grader.rubric_ai import _ensure_env
    _ensure_env()
    import anthropic
    return anthropic.Anthropic()


# ── 1. understand the challenge (LLM) ────────────────────────────────────────
_PARSE_SCHEMA = {
    "type": "object",
    "properties": {
        "challenged_sections": {"type": "array", "items": {"type": "string"},
            "description": "Section codes the student is disputing (e.g. 'S4'); [] if unclear/all."},
        "claim_type": {"type": "string", "enum": [
            "access", "missing_files", "content_not_seen", "invalid_reason",
            "perception", "vague", "other"]},
        "claims": {"type": "array", "items": {"type": "string"},
            "description": "Each concrete factual claim the student makes, verbatim-ish."},
        "shared_link": {"type": "string", "description": "Any Drive/repo link in the message, else ''."},
        "summary": {"type": "string", "description": "One-line neutral summary of what they want."},
    },
    "required": ["challenged_sections", "claim_type", "claims", "shared_link", "summary"],
    "additionalProperties": False,
}
_PARSE_SYS = (
    "You triage a student's grade-challenge ticket for a capstone. Extract, factually and "
    "without judging validity: which sections they dispute, the type of claim, each concrete "
    "claim, and any link they shared. claim_type guide: 'access' = they re-shared / fixed "
    "permissions; 'missing_files' = a file they submitted was marked missing/not received; "
    "'content_not_seen' = their content (screenshots, links, a section) wasn't accounted for; "
    "'invalid_reason' = the stated deduction reason is factually wrong (dates, a requirement "
    "that doesn't exist); 'perception' = they feel the feedback is positive but marks are low; "
    "'vague' = no specific point.")


def _parse_challenge(message: str, part_keys: list[str]) -> dict:
    msg = _client().messages.create(
        model=_MODEL, max_tokens=1500,
        system=_PARSE_SYS + "\nSection codes available: " + ", ".join(k.split("_")[0] for k in part_keys),
        output_config={"format": {"type": "json_schema", "schema": _PARSE_SCHEMA}},
        messages=[{"role": "user", "content": "TICKET MESSAGE:\n" + message.strip()}])
    text = next((b.text for b in msg.content if b.type == "text"), "{}")
    return json.loads(text)


# ── 2. gather evidence (deterministic diagnostics) ───────────────────────────
_FALSE_REASON_SIGNS = [
    ("future-dated", "date-sensitivity"), ("placeholder", "placeholder-label"),
    ("not present in submission", "not-seen"), ("not provided", "not-seen"),
    ("not submitted", "not-seen"), ("no submission", "not-seen"),
    ("could not be", "not-seen"), ("not fully included", "not-seen"),
    ("video", "phantom-requirement"), ("live walkthrough", "phantom-requirement"),
    ("live demo", "phantom-requirement"), ("session recording", "phantom-requirement"),
    ("link1", "placeholder-label"),
]


def _diagnose(s, ev, st, parse: dict, new_link: str) -> dict:
    """Hard evidence about whether our grading under-served this student."""
    meta = st.download_meta_json or {}
    dl = meta.get("download", {})
    defects, notes = [], []

    # (a) download completeness — the #1 defect class
    n_listed = sum(l.get("n_listed", 0) for l in dl.get("links", [])) or dl.get("n_files", 0)
    n_files = dl.get("n_files", sum(1 for _ in (eval_data_dir(ev.slug) / "downloads" / st.student_code).rglob("*")
                                    if _.is_file()) if (eval_data_dir(ev.slug) / "downloads" / st.student_code).exists() else 0)
    if st.download_status in ("restricted", "partial", "failed", "auth", "not_gradeable"):
        defects.append("download_" + st.download_status)
        notes.append(f"download status was '{st.download_status}' — submission was not fully retrieved at grading")
    if any(l.get("n_failed") for l in dl.get("links", [])):
        defects.append("partial_download")
        notes.append("some files in the folder failed to download originally")

    # (b) stated-reason signatures on the challenged sections
    challenged = set(parse.get("challenged_sections") or [])
    for g in repo.grades_for(s, st.id):
        code = g.part_key.split("_")[0]
        if challenged and code not in challenged:
            continue
        fb = (g.feedback or "").lower()
        below = g.score < g.max - 1e-9
        if below and not (g.feedback or "").strip():
            defects.append(f"{g.part_key}:blank_feedback")
            notes.append(f"{code} scored {g.score}/{g.max} with NO feedback (section may have been truncated)")
        for phrase, tag in _FALSE_REASON_SIGNS:
            if below and phrase in fb:
                defects.append(f"{g.part_key}:{tag}")
                notes.append(f"{code} deduction cites '{phrase}' — a known false-deduction pattern; verify")
                break

    # (c) multi-link submission under-parsed to fewer links than are really present
    from grader.download import extract_links
    stored = (meta.get("n_links") or 0)
    real = len(_all_links(st.submission_raw, new_link))
    if real > 1 and real > stored:
        defects.append("multi_link_underparse")
        notes.append(f"submission has {real} links but only {stored} were fetched — the rest of the "
                     "work (e.g. later parts) was never graded")
    # (d) several sections scored exactly 0 while others didn't → likely missing content, not a zero
    grades = list(repo.grades_for(s, st.id))
    zeros = [g for g in grades if g.score == 0]
    if grades and 0 < len(zeros) < len(grades):
        defects.append("zero_sections")
        notes.append(f"{len(zeros)} of {len(grades)} sections scored 0 — check whether that content "
                     "was actually retrieved")

    # a re-shared/new link, restricted original, OR any of the above → warrants a full re-fetch + regrade
    needs_refetch = bool(new_link or parse.get("shared_link")) or st.download_status != "ok" \
        or "multi_link_underparse" in defects or "zero_sections" in defects
    return {"defects": sorted(set(defects)), "notes": notes,
            "needs_refetch": needs_refetch, "n_listed": n_listed, "n_files": n_files,
            "n_links_stored": stored, "n_links_real": real,
            "download_status": st.download_status}


def _all_links(submission_raw: str, extra: str) -> list[str]:
    """Every distinct submission link — from the stored cell AND anything the
    student pasted in the ticket. A multi-repo submission (e.g. one repo per part)
    must be fetched IN FULL, not just the first link."""
    from grader.download import extract_links
    from grader.intake import links_from_raw
    raws = [l["raw"] for l in links_from_raw(submission_raw or "")]
    for x in extract_links(extra or ""):
        if x not in raws:
            raws.append(x)
    return list(dict.fromkeys(r for r in raws if r))


def _refetch_all(ev, st, extra: str) -> dict:
    """Re-parse ALL submission links and re-download each into link1..linkN with the
    FIXED downloader (reports partial honestly, extracts scheme-less links). Backs
    up the pre-ticket download for audit. Updates the student's download meta."""
    import shutil
    from grader.download import _sa_key_path, _fetch_one, classify
    raws = _all_links(st.submission_raw, extra)
    if not raws:
        return {"n_links": 0, "results": []}
    dest = eval_data_dir(ev.slug) / "downloads" / st.student_code
    bak = dest.with_name(st.student_code + "_PRE_TICKET")
    if dest.exists() and not bak.exists():
        shutil.copytree(dest, bak)
    if dest.exists():
        shutil.rmtree(dest)
    sa = _sa_key_path((ev.config_json or {}))
    single = len(raws) == 1
    links_meta, results = [], []
    for i, raw in enumerate(raws, 1):
        d = dest if single else dest / f"link{i}"
        res = _fetch_one(sa, raw, d)
        results.append({"raw": raw, **{k: res.get(k) for k in ("status", "n_files")}})
        t, v = classify(raw)
        links_meta.append({"question_id": "", "raw": raw, "type": t, "value": v,
                           "dl_status": res["status"], "local_dir": str(d)})
    s = get_session()
    st2 = repo.get_student(s, ev.id, st.student_code)
    meta = dict(st2.download_meta_json or {})
    meta["mode"] = "single" if single else "per_part"
    meta["n_links"] = len(raws)
    meta["links"] = links_meta
    meta["download"] = {"status": "ok", "links": results,
                        "n_files": sum(r.get("n_files") or 0 for r in results)}
    st2.download_meta_json = meta
    st2.download_status = "ok" if all(r["status"] in ("ok",) for r in results) else \
        ("partial" if any(r["status"] == "ok" for r in results) else "failed")
    s.commit()
    return {"n_links": len(raws), "results": results, "status": st2.download_status}


# ── 3. draft the reply (LLM, guarded) ────────────────────────────────────────
_REPLY_SYS = (
    "You write the official reply that closes a student's capstone grade-challenge ticket. "
    "Rules (strict):\n"
    "- Neutral, professional, warm. Address the student by first name if available.\n"
    "- State the outcome plainly: score revised (give old→new and which sections moved) OR "
    "reviewed with no change.\n"
    "- NEVER expose internal/technical failure, bugs, downloads, parsing, or 'our system'. "
    "Do NOT blame the student. Frame as 'on re-evaluation, your submission was assessed in full'.\n"
    "- If a specific claim of theirs is correct, acknowledge it directly.\n"
    "- If some marks still stand, explain the REMAINING deductions honestly and rubric-grounded.\n"
    "- NEVER state or imply that the student's files, repositories, sections, links or content were "
    "missing, inaccessible, 'not present', or 'not submitted'. Their submission is complete and was "
    "assessed in full. (If we couldn't see something earlier, that is never framed as their fault.)\n"
    "- Include the report-card link if one is provided.\n"
    "- End by closing the ticket (state it is resolved with the score revised, or reviewed with no change).\n"
    "- Write in plain, natural human prose. Do NOT use em dashes (—) or en dashes (–) anywhere; "
    "use commas, periods, or parentheses instead.\n"
    "- 120-220 words. No markdown headers.")


def _draft_reply(student_name, parse, verdict, old_total, new_total, section_moves, remaining, card_url):
    ctx = {
        "student_name": student_name or "",
        "their_claim": parse.get("summary", ""), "claims": parse.get("claims", []),
        "outcome": verdict, "old_total": old_total, "new_total": new_total,
        "sections_changed": section_moves, "remaining_deductions": remaining,
        "card_url": card_url or "",
    }
    msg = _client().messages.create(
        model=_MODEL, max_tokens=900, system=_REPLY_SYS,
        messages=[{"role": "user", "content": "Write the reply. Facts:\n" + json.dumps(ctx, indent=2)}])
    reply = "".join(b.text for b in msg.content if getattr(b, "type", None) == "text").strip()
    return _guard_reply(reply, card_url)


_BANNED = ["bug", "our system", "download failed", "parser", "parsing", "truncat",
           "glitch", "technical error", "malfunction", "the tool", "the model"]
# blame-framing: never tell a student their own work was missing/not submitted
_BLAME = ["not present in the submit", "not present in your", "not present in the repositor",
          "were not submitted", "was not submitted", "not submitted for", "not included in your",
          "not accessible", "could not access your", "files were missing", "content was missing"]


def _guard_reply(reply: str, card_url) -> str:
    reply = G.strip_em_dashes(reply)                  # human-written, no em/en dashes
    low = reply.lower()
    flags = [w for w in _BANNED + _BLAME if w in low]
    if card_url and card_url not in reply:
        reply += f"\n\nUpdated report card: {card_url}"
    return reply if not flags else "⚠ REVIEW (flagged phrasing: " + ", ".join(flags) + ")\n\n" + reply


# ── orchestration ────────────────────────────────────────────────────────────
def analyze(eval_slug: str, student_code: str, message: str, new_link: str = "",
            progress: Optional[Callable[[str], None]] = None) -> dict:
    """Diagnose + regrade-propose + draft reply. WRITES NOTHING."""
    log = progress or (lambda m: None)
    s = get_session()
    ev = repo.get_eval(s, eval_slug)
    if not ev:
        return {"error": f"no eval '{eval_slug}'"}
    st = repo.get_student(s, ev.id, student_code)
    if not st:
        return {"error": f"no student '{student_code}' in {eval_slug}"}
    parts = repo.parts(s, ev.id)
    old_total = st.total_score
    old_sec = {g.part_key: g.score for g in repo.grades_for(s, st.id)}

    log("reading the ticket…")
    parse = _parse_challenge(message, [p.key for p in parts])
    link = (new_link or parse.get("shared_link") or "").strip()

    diag = _diagnose(s, ev, st, parse, link)
    refetch = None
    if diag["needs_refetch"]:
        log(f"re-downloading ALL {diag['n_links_real']} submission link(s)…")
        refetch = _refetch_all(ev, st, link)

    # decide: regrade iff there is a plausible our-side cause (defects / re-fetch / restricted)
    should_regrade = bool(diag["defects"]) or diag["needs_refetch"] or parse["claim_type"] in (
        "missing_files", "content_not_seen", "invalid_reason", "access")

    new_total, r, section_moves, remaining = old_total, None, [], []
    if should_regrade:
        log("regrading (single pass, n=1) on the fixed engine…")
        try:
            r = G.regrade_result(ev.id, student_code, progress=lambda m: None, source="ticket")
        except Exception as e:
            return {"error": f"regrade failed: {e}", "parse": parse, "diagnosis": diag}
        # NO-DECREASE at the total level
        proposed = round(r["total"], 2)
        if proposed > (old_total or 0) + 1e-9:
            new_total = proposed
            for p in parts:
                nb = r["sections"][p.key]["score"]; ob = old_sec.get(p.key, 0)
                if abs(nb - ob) >= 0.5:
                    section_moves.append({"section": p.key.split("_")[0], "old": ob, "new": nb})
            for p in parts:                                 # remaining deductions on the new grade
                for d in r["sections"][p.key].get("deductions", []):
                    remaining.append({"section": p.key.split("_")[0], **d})

    changed = new_total is not None and old_total is not None and new_total > old_total
    delta = round((new_total or old_total or 0) - (old_total or 0), 2)
    verdict = "revised" if changed else "no_change"
    # confidence / escalation
    escalate = (delta > 15) or (parse["claim_type"] == "perception" and not diag["defects"]) \
        or (should_regrade and not changed and parse["claim_type"] in ("missing_files", "content_not_seen"))

    draft = _draft_reply(st.name, parse, verdict, old_total, new_total, section_moves, remaining,
                         card_url=None)  # card only exists after apply(); link injected then
    return {
        "eval": eval_slug, "student_code": student_code, "student_name": st.name,
        "parse": parse, "diagnosis": diag, "refetch": refetch,
        "verdict": verdict, "changed": changed, "delta": delta,
        "old_total": old_total, "new_total": new_total, "total_max": ev.total_marks,
        "section_moves": section_moves, "remaining": remaining,
        "escalate": bool(escalate),
        "draft_reply": draft,
        "regrade": r,                                       # kept so apply() can persist without re-grading
    }


def apply(eval_slug: str, student_code: str, proposal: dict) -> dict:
    """Persist an approved proposal (score, card→regrades/, sheet, audit). Does NOT
    post to the LMS — the human submits the drafted reply. No-decrease re-checked."""
    r = proposal.get("regrade")
    if not (proposal.get("changed") and r):
        return {"applied": False, "reason": "no increase to apply (no-decrease) — score stands"}
    s = get_session()
    ev = repo.get_eval(s, eval_slug); st = repo.get_student(s, ev.id, student_code)
    parts = repo.parts(s, ev.id); old = st.total_score
    if round(r["total"], 2) <= (old or 0) + 1e-9:
        return {"applied": False, "reason": "no-decrease: recomputed total not higher"}
    G._write_grades(s, st, r, parts, ev.grader_model, "ticket_regrade")
    repo.audit(s, "ticket_regrade", eval_id=ev.id, student_id=st.id,
               before={"total": old}, after={"total": r["total"],
               "claim": proposal.get("parse", {}).get("summary", "")})
    s.commit(); eid = ev.id
    report.report_one(eid, student_code)
    url = None
    if storage.enabled():
        with get_session() as s2:
            ev2 = repo.get_eval(s2, eval_slug); st2 = repo.get_student(s2, ev2.id, student_code)
            meta = dict(st2.download_meta_json or {}); card = meta["report"]["card_file"]
            key = f"{storage._cfg()['prefix']}/{eval_slug}/regrades/{student_code}-{secrets.token_hex(4)}.pdf"
            url = storage.upload(card, key)
            meta["regrade"] = {"url": url, "s3_key": key, "old_total": old, "new_total": r["total"]}
            st2.download_meta_json = meta; s2.commit()
    reply = _guard_reply(proposal.get("draft_reply", ""), url)
    return {"applied": True, "old_total": old, "new_total": r["total"],
            "card_url": url, "draft_reply": reply}
