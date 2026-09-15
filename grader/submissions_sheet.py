"""Load student submissions for an eval from a tab in the Outcomes master sheet.

The master tracker sheet is already fully readable by the Drive service account,
so — rather than share each per-eval submission sheet (which trips Masai's
external-sharing block) — the grader reads a tab in that master sheet, named by
the eval slug, into the DB as Student rows.

Submission-sheet SHAPES are NOT fixed and will keep evolving; this parser is
built to grow. Currently handled:

  * LONG / per-question (the platform export): one row per (student, question),
    columns `User Code | Question ID | Answer Type | Answer Raw`. A student's
    rows repeat the same set of Question IDs; the User Code appears once per
    block (position varies — first row in one export, last in another), so we
    group by QUESTION-ID CYCLE rather than assuming where the code sits. Each
    student's links are collected and the submission MODE is inferred:
      - single    : one distinct link covers every question (one repo/folder)
      - per_part  : a different link per question (N repos/folders)
      - partial   : some questions answered, some blank
      - none      : nothing submitted
  * WIDE / one-row-per-student: a plain id + submission-link sheet.

Add new shapes by extending `_classify_shape` / `parse_submissions`.
"""
from __future__ import annotations

import collections

from app.db import repo
from app.db.models import Student
from app.db.session import get_session
from grader.download import classify
from grader.intake import detect_columns, links_from_raw, students_from_rows
from grader.pending_sheet import SHEET_ID, read_rows


# ── column detection ─────────────────────────────────────────────────────────
def _question_col(cols):
    low = {c: c.lower() for c in cols}
    for needle in ("question id", "question", "qid", "q id"):
        for c in cols:
            if needle in low[c]:
                return c
    return None


def _answer_col(cols):
    """Prefer the raw-answer/link column. Never the 'Answer Type' column, and never a
    grading column (Score/MaxMarks/answerScore/Feedback) — 'answerScore' must not be
    mistaken for the answer just because it contains 'answer'."""
    low = {c: c.lower() for c in cols}
    # never the Answer-Type col, a grading col, or an id col ('User Code'/'Student Code'/'Roll')
    skip = ("type", "score", "marks", "feedback", "user code", "student code",
            "usercode", "user_code", "roll")
    for needle in ("answer raw", "submission link", "submission", "github", "repo",
                   "drive", "solution", "code", "link", "url", "raw", "answer"):
        for c in cols:
            if needle in low[c] and not any(sk in low[c] for sk in skip):
                return c
    return None


# ── mode inference ───────────────────────────────────────────────────────────
def _mode(links: list[dict]) -> tuple[str, str]:
    """links = [{question_id, raw}]. → (mode, submission_type)."""
    filled = [l for l in links if l["raw"]]
    uniq = list(dict.fromkeys(l["raw"] for l in filled))
    if not filled:
        return "none", "empty"
    types = {classify(r)[0] for r in uniq}
    stype = next(iter(types)) if len(types) == 1 else "mixed"
    if len(uniq) == 1:
        return "single", stype
    if len(filled) < len(links):
        return "partial", stype
    return "per_part", stype


def _finalize(block: dict) -> dict:
    # links are already cleaned/classified in _parse_long (via links_from_raw)
    mode, stype = _mode(block["links"])
    uniq = list(dict.fromkeys(l["raw"] for l in block["links"] if l["raw"]))
    n_q = len({l["question_id"] for l in block["links"] if l["question_id"]}) or len(block["links"])
    return {
        "student_code": block["code"], "name": block["name"], "email": block["email"],
        "submission_raw": "\n".join(uniq),          # distinct clean links, one per line
        "submission_type": stype,
        "mode": mode,
        "n_links": len(uniq),
        "n_questions": n_q,
        "links": block["links"],
    }


# ── parsers per shape ────────────────────────────────────────────────────────
def _parse_long(rows, id_col, q_col, ans_col, name_col, email_col) -> list[dict]:
    blocks, cur = [], None
    for r in rows:
        qid = (r.get(q_col) or "").strip()
        code = (r.get(id_col) or "").strip()
        raw = (r.get(ans_col) or "").strip()
        name = (r.get(name_col) or "").strip() if name_col else ""
        email = (r.get(email_col) or "").strip() if email_col else ""
        if cur is None or (qid and qid in cur["seen"]):     # question cycle → new student
            cur = {"code": "", "name": "", "email": "", "links": [], "seen": set()}
            blocks.append(cur)
        if code and not cur["code"]:
            cur["code"] = code
        if name and not cur["name"]:
            cur["name"] = name
        if email and not cur["email"]:
            cur["email"] = email
        cleaned = links_from_raw(raw)                       # strip HTML, split multi-links
        if cleaned:
            for l in cleaned:
                cur["links"].append({"question_id": qid, **l})
        else:
            cur["links"].append({"question_id": qid, "raw": "", "type": "empty", "value": ""})
        if qid:
            cur["seen"].add(qid)
    return [_finalize(b) for b in blocks if b["code"]]       # drop unlabeled blocks


def _parse_wide(rows) -> list[dict]:
    out = []
    for w in students_from_rows(rows):                      # value-aware cols + clean links
        links = [{"question_id": "", **l} for l in w["links"]]
        n = len(links)
        mode = "none" if n == 0 else ("single" if n == 1 else "per_part")
        out.append({"student_code": w["student_code"], "name": w["name"], "email": w["email"],
                    "submission_raw": w["submission_raw"], "submission_type": w["submission_type"],
                    "mode": mode, "n_links": n, "n_questions": max(1, n), "links": links})
    return out


def parse_submissions(rows: list[dict]) -> tuple[list[dict], str]:
    """Detect the sheet shape and parse → (students, shape_name)."""
    if not rows:
        return [], "empty"
    cols = list(rows[0].keys())
    q_col, ans_col = _question_col(cols), _answer_col(cols)
    if q_col and ans_col:                                   # LONG / per-question export
        cm = detect_columns(rows, col_map={"submission": ans_col}, exclude=[q_col])
        if not cm.get("id"):
            raise ValueError("no student id/email/roll column found — add a header like "
                             "'User Code', 'Roll No', or 'Email'")
        return _parse_long(rows, cm["id"], q_col, ans_col, cm["name"], cm["email"]), "long"
    return _parse_wide(rows), "wide"                        # WIDE / one-row-per-student


# ── load into DB ─────────────────────────────────────────────────────────────
def load_submissions(eval_id: int, tab: str, sheet_id: str = SHEET_ID) -> dict:
    try:
        rows = read_rows(sheet_id, tab)
    except RuntimeError as e:
        if "Unable to parse range" in str(e):
            raise ValueError(f"no tab named '{tab}' in the master sheet — "
                             f"create a tab named exactly '{tab}' with a header row")
        raise
    students, shape = parse_submissions(rows)
    if not students:
        raise ValueError(f"tab '{tab}' has a header but no student rows")

    s = get_session()
    ev0 = repo.get_eval_by_id(s, eval_id)
    mb_cfg = (ev0.config_json or {}).get("metabase", {})
    qmap = mb_cfg.get("question_map", {})                   # question_id -> part_key
    # Opt-in, per-eval flag for the "one combined repo, N rubric parts" shape
    # (e.g. a single-repo multi-part capstone where the LMS only has ONE question/
    # link total, but the rubric was split into several parts). Off by default so
    # every other eval's behavior is completely unchanged; when on, a student whose
    # question_map-derived part_links all point to the SAME single link gets that
    # link replicated onto every declared part, reusing the EXISTING shared-link
    # grouping in grade._perq_groups (already handles "same repo -> graded once,
    # covering all parts in one call") - no grading-path change needed at all.
    shared_repo = bool(mb_cfg.get("shared_repo_all_parts"))
    all_part_keys = [p.key for p in repo.parts(s, eval_id)] if shared_repo else []
    added = updated = 0
    for st in students:
        empty = st["mode"] == "none"
        # per-question evals: map each link to its PART (via question_id) so download +
        # grading fetch/score each part's own repo against its own rubric.
        part_links = {}
        if qmap:
            candidates = {}                          # pk -> [links in order] (same question_id can carry >1 URL)
            for l in st["links"]:
                pk = qmap.get(l.get("question_id"))
                if pk and l.get("raw"):
                    candidates.setdefault(pk, []).append(l)
            for pk, cands in candidates.items():
                # Prefer an actual GitHub repo link over any OTHER url a student pastes
                # alongside it (a live demo/hosted-app link, a Swagger docs page, a
                # localhost reference) - blindly taking the LAST link in the cell (the
                # old behavior) silently graded students against the wrong URL entirely.
                # Confirmed live on iitp-sdaieng-2602-81303: a student with a real,
                # valid GitHub repo link PLUS a Railway-hosted docs-page link got
                # graded 0/100 against the docs page; two others got marked
                # "restricted" against a Render URL / bare localhost that was never
                # their real submission at all.
                github_links = [c for c in cands if c.get("type") == "github"]
                chosen = github_links[0] if github_links else cands[-1]
                part_links[pk] = chosen["raw"]
        if shared_repo and len(set(part_links.values())) == 1:
            only_link = next(iter(part_links.values()))
            part_links = {pk: only_link for pk in all_part_keys}
        meta = {"mode": st["mode"], "n_links": st["n_links"],
                "n_questions": st["n_questions"], "links": st["links"]}
        if part_links:
            meta["part_links"] = part_links
        existing = repo.get_student(s, eval_id, st["student_code"])
        if existing:
            prev = dict(existing.download_meta_json or {})
            existing.name = st["name"] or existing.name          # refetch fills metadata
            existing.email = st["email"] or existing.email
            existing.submission_raw = st["submission_raw"] or existing.submission_raw
            if st["submission_type"] and st["submission_type"] != "empty":
                existing.submission_type = st["submission_type"]
            merged = {**prev, **meta}
            # MERGE part_links (never replace) so a row Ops appends for ONE missing
            # part ADDS it without wiping the parts already on the student.
            if part_links or prev.get("part_links"):
                merged["part_links"] = {**(prev.get("part_links") or {}), **part_links}
            # union the flat links list too (dedupe by raw)
            seen = {l.get("raw") for l in (prev.get("links") or [])}
            merged["links"] = (prev.get("links") or []) + [l for l in st["links"] if l.get("raw") not in seen]
            changed = merged.get("part_links") != prev.get("part_links") or merged.get("links") != prev.get("links")
            existing.download_meta_json = merged
            if changed and existing.download_status != "ok":     # don't clobber a done download
                existing.download_status = "not_submitted" if empty else "pending"
                if existing.grade_status != "graded":
                    existing.grade_status = "not_submitted" if empty else "pending"
            updated += 1
        else:
            s.add(Student(eval_id=eval_id, student_code=st["student_code"],
                          name=st["name"], email=st["email"],
                          submission_raw=st["submission_raw"],
                          submission_type=st["submission_type"], download_meta_json=meta,
                          download_status="not_submitted" if empty else "pending",
                          grade_status="not_submitted" if empty else "pending"))
            added += 1

    ev = repo.get_eval_by_id(s, eval_id)
    cfg = dict(ev.config_json or {})
    cfg["submissions_tab"] = tab
    ev.config_json = cfg
    modes = collections.Counter(st["mode"] for st in students)
    missing_meta = sum(1 for st in students if st["mode"] != "none"
                       and (not st["name"] or not st["email"]))
    repo.audit(s, "load_submissions", eval_id=eval_id,
               after={"tab": tab, "shape": shape, "added": added, "updated": updated,
                      "total": len(students), "modes": dict(modes)})
    s.commit()
    return {"added": added, "updated": updated, "total": len(students), "shape": shape,
            "modes": dict(modes), "missing_meta": missing_meta, "tab": tab}
