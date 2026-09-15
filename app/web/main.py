"""GradePilot dashboard — FastAPI + Jinja2. Local, single-user.

Run:  .venv/bin/uvicorn app.web.main:app --reload --port 8000
"""
from __future__ import annotations

import collections
import datetime
import statistics
from pathlib import Path

from fastapi import Depends, FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.db import repo
from app.db.session import eval_data_dir, get_db, init_db, safe_filename
from app.jobs import runner

HERE = Path(__file__).resolve().parent
app = FastAPI(title="GradePilot")
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
templates = Jinja2Templates(directory=HERE / "templates")


def _asset(name: str) -> str:
    """Cache-busting static URL — ?v=<mtime> so edits are always fetched fresh."""
    try:
        v = int((HERE / "static" / name).stat().st_mtime)
    except OSError:
        v = 0
    return f"/static/{name}?v={v}"


templates.env.globals["asset"] = _asset


def _safe_href(url: str) -> str:
    """Only ever emit http(s) as a clickable href — these render student-
    submitted submission links, so a 'javascript:' URI must never reach an
    <a href>; HTML-escaping alone doesn't stop that (the browser executes
    the scheme, not the escaped entities)."""
    return url if isinstance(url, str) and url.lower().startswith(("http://", "https://")) else "#"


templates.env.filters["safe_href"] = _safe_href


def render(request, name, ctx=None):
    return templates.TemplateResponse(request, name, ctx or {})


@app.on_event("startup")
def _startup():
    init_db()
    _sweep_orphaned_jobs()


def _sweep_orphaned_jobs():
    """Any job still 'queued'/'running' at process startup belongs to a PREVIOUS
    process that died or was restarted (a fresh process can't have legitimately
    started them) — left as-is they're stuck forever, spinning the loader/poller
    with no way to ever reach 'done'. Mark them errored so a restart always starts
    clean, matching the same "stop and clear, then resume fresh" behaviour we
    already guarantee for grading runs (see _FailureBreaker in grader/grade.py)."""
    from sqlalchemy import select
    from app.db.models import Job
    from app.db.session import get_session
    s = get_session()
    stale = list(s.scalars(select(Job).where(Job.status.in_(("queued", "running")))))
    for j in stale:
        j.status = "error"
        j.progress = "error"
        j.log = "Interrupted by a server restart — please re-run this action."
    if stale:
        s.commit()


def _eval_or_404(s: Session, slug: str):
    ev = repo.get_eval(s, slug)
    if not ev:
        raise HTTPException(404, f"no eval '{slug}'")
    return ev


def _summary(s: Session, ev) -> dict:
    studs = repo.students(s, ev.id)
    graded = [st.total_score for st in studs if st.grade_status == "graded" and st.total_score is not None]
    return {
        "n": len(studs),
        "dl": dict(collections.Counter(st.download_status for st in studs)),
        "grade": dict(collections.Counter(st.grade_status for st in studs)),
        "types": dict(collections.Counter(st.submission_type for st in studs)),
        "scores": {
            "n": len(graded),
            "mean": round(statistics.mean(graded), 1) if graded else 0,
            "median": round(statistics.median(graded), 1) if graded else 0,
            "min": round(min(graded), 1) if graded else 0,
            "max": round(max(graded), 1) if graded else 0,
        },
    }


# ── evals ───────────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
def root():
    return RedirectResponse("/evals")


@app.get("/evals", response_class=HTMLResponse)
def evals_list(request: Request, s: Session = Depends(get_db)):
    rows = [{"ev": ev, "n": len(repo.students(s, ev.id)),
             "wt": _wt(ev), "n_parts": len(repo.parts(s, ev.id))}
            for ev in repo.list_evals(s)]
    return render(request, "evals.html", {"request": request, "rows": rows})


@app.get("/evals/new", response_class=HTMLResponse)
def new_eval_form(request: Request):
    return render(request, "new_eval.html", {"request": request})


@app.post("/evals/new")
async def new_eval(request: Request, slug: str = Form(...), title: str = Form(...),
                   model: str = Form("claude-sonnet-5"), normalize: int = Form(10),
                   total: str = Form(""), sheet: UploadFile = None,
                   rubric: UploadFile = None, ps: UploadFile = None):
    from grader.intake import ingest
    slug = slug.strip()
    intake_dir = eval_data_dir(slug) / "intake"; intake_dir.mkdir(parents=True, exist_ok=True)

    async def save(up: UploadFile, name: str):
        p = intake_dir / name
        p.write_bytes(await up.read())
        return str(p)

    sheet_p = await save(sheet, "submissions.csv")
    rubric_p = await save(rubric, "rubric.md")
    ps_p = await save(ps, "problem_statement.md") if ps and ps.filename else None
    try:
        ingest(slug, title.strip(), sheet_p, rubric_p, ps_p,
               total_marks=int(total) if total.strip() else None,
               normalize_to=normalize, grader_model=model.strip())
    except Exception as e:
        return render(request, "new_eval.html",
            {"request": request, "error": str(e), "form": {"slug": slug, "title": title}})
    return RedirectResponse(f"/evals/{slug}", status_code=303)


def _eval_ctx(request: Request, s: Session, ev) -> dict:
    """Full context for the eval page — reused by the fragment endpoints so a live
    poll re-renders the roster/status with the exact same data the full page shows."""
    job = runner.latest_job(ev.id)
    studs = repo.students(s, ev.id)
    submitted = [st for st in studs if st.submission_type != "empty"]
    n_missing_meta = sum(1 for st in submitted if not st.name or not st.email)
    n_graded = sum(1 for st in studs if st.grade_status == "graded")
    n_cards = sum(1 for st in studs if (st.download_meta_json or {}).get("report", {}).get("has_card"))
    n_nosub = sum(1 for st in studs if st.submission_type == "empty")
    n_pending = sum(1 for st in studs if st.submission_type != "empty" and st.grade_status != "graded")
    parts = repo.parts(s, ev.id)
    from grader.grade import _is_per_question
    per_question = _is_per_question(ev, parts)         # 1 link per PART vs 1 link total
    gstuds, gmap = [], {}
    if studs and parts:                                # full roster: graded → pending → no-submission
        def _rank(st):
            tier = 0 if st.grade_status == "graded" else (2 if st.submission_type == "empty" else 1)
            return (tier, -(st.total_score or 0), st.student_code)
        gstuds = sorted(studs, key=_rank)
        for g in repo.all_grades(s, ev.id):
            gmap.setdefault(g.student_id, {})[g.part_key] = g
    return {"request": request, "ev": ev, "summary": _summary(s, ev), "job": job,
            "wt": _wt(ev), "per_question": per_question,
            "n_parts": len(parts), "parts": parts, "n_students": len(studs),
            "n_submitted": len(submitted), "n_missing_meta": n_missing_meta,
            "n_graded": n_graded, "n_cards": n_cards, "n_nosub": n_nosub, "n_pending": n_pending,
            "gstuds": gstuds, "gmap": gmap,
            "n_downloaded": sum(1 for st in studs if st.download_status == "ok"),
            "pushed": (ev.config_json or {}).get("grades_pushed"),
            "preview": (ev.config_json or {}).get("preview")}


@app.get("/evals/{slug}", response_class=HTMLResponse)
def eval_detail(request: Request, slug: str, s: Session = Depends(get_db)):
    ev = _eval_or_404(s, slug)
    return render(request, "eval_detail.html", _eval_ctx(request, s, ev))


@app.get("/evals/{slug}/roster", response_class=HTMLResponse)
def eval_roster(request: Request, slug: str, s: Session = Depends(get_db)):
    """Just the Results roster card — polled live during grading/download so rows
    flip to graded/downloaded without a manual refresh."""
    ev = _eval_or_404(s, slug)
    return render(request, "_roster.html", _eval_ctx(request, s, ev))


@app.get("/evals/{slug}/cost", response_class=HTMLResponse)
def eval_cost(request: Request, slug: str, s: Session = Depends(get_db)):
    """Cost modal body — the saved sample + real cost runs for this eval."""
    ev = _eval_or_404(s, slug)
    cfg = ev.config_json or {}
    return render(request, "_cost.html",
        {"request": request, "ev": ev, "cost_runs": cfg.get("cost_runs", []),
         "preview": cfg.get("preview"), "finalize_msg": None})


@app.post("/evals/{slug}/cost/finalize", response_class=HTMLResponse)
def eval_cost_finalize(request: Request, slug: str, run_type: str = Form("real"),
                       s: Session = Depends(get_db)):
    """Button action: write this run's summary to the 'API Cost Log per Eval' tab
    and clear this eval's per-call detail rows from 'API Cost Log'."""
    ev = _eval_or_404(s, slug)
    cfg = ev.config_json or {}
    runs = cfg.get("cost_runs", [])
    run = next((r for r in runs if r.get("type") == run_type), None)
    msg = None
    if not run:
        msg = {"ok": False, "err": f"no saved {run_type} cost run yet"}
    else:
        from grader.metabase import write_eval_cost_summary, clear_cost_log_for_eval
        try:
            res = write_eval_cost_summary(ev.slug, ev.title, run)
            removed = clear_cost_log_for_eval(ev.slug)
            msg = {"ok": True, "url": res["url"], "removed": removed}
        except Exception as e:
            msg = {"ok": False, "err": str(e)[:140]}
    return render(request, "_cost.html",
        {"request": request, "ev": ev, "cost_runs": runs,
         "preview": cfg.get("preview"), "finalize_msg": msg})


def _student_remark(st) -> str:
    if st.grade_status == "graded":
        return ""
    if st.submission_type == "empty":
        return "No Submissions Found"
    if st.download_status != "ok":
        return "Submitted — download pending"
    return "Submitted — pending grading"


def _results_rows(s, ev, parts):
    """Master-CSV-shaped rows for ALL students (graded + not) with a Remarks col."""
    gmap = {}
    for g in repo.all_grades(s, ev.id):
        gmap.setdefault(g.student_id, {})[g.part_key] = g
    rows = []
    for st in sorted(repo.students(s, ev.id), key=lambda x: x.student_code):
        gm = gmap.get(st.id, {})
        graded = st.grade_status == "graded"
        row = {"User Code": st.student_code, "Name": st.name, "Email": st.email,
               "Total_Score": st.total_score if graded else "",
               "Total_Max": ev.total_marks,
               "Scaled": (round((st.total_score or 0) / ev.total_marks * ev.normalize_to, 2)
                          if graded and ev.total_marks else ""),
               "Overall": st.overall_feedback if graded else "",
               "Remarks": _student_remark(st),
               "Report_URL": (st.download_meta_json or {}).get("report", {}).get("url", "")}
        for p in parts:
            g = gm.get(p.key)
            row[f"{p.key}_score"] = (g.score if g else 0) if graded else ""
            row[f"{p.key}_max"] = p.max_marks
            row[f"{p.key}_feedback"] = (g.feedback if g else "") if graded else ""
        rows.append(row)
    return rows


@app.post("/evals/{slug}/student/{code}/set-link")
async def set_link(slug: str, code: str, request: Request, s: Session = Depends(get_db)):
    """Attach a submission link (or one link per PART, for per-question evals) to a
    student who had none (late submission)."""
    from grader.download import classify
    from grader.grade import _is_per_question
    ev = _eval_or_404(s, slug)
    st = repo.get_student(s, ev.id, code)
    if not st:
        return RedirectResponse(f"/evals/{slug}", status_code=303)
    form = await request.form()
    parts = repo.parts(s, ev.id)

    if _is_per_question(ev, parts):                        # one input per part -> part_links
        part_links = {}
        for p in parts:
            v = (form.get(f"link_{p.key}") or "").strip()
            if v:
                part_links[p.key] = v
        if part_links:
            first = next(iter(part_links.values()))
            st.submission_raw = "\n".join(f"{k}: {v}" for k, v in part_links.items())
            st.submission_type = classify(first)[0] or "url"
            meta = dict(st.download_meta_json or {})
            meta["part_links"] = part_links
            meta.update({"mode": "per_question", "n_links": len(part_links), "n_questions": len(parts),
                         "links": [{"question_id": "", "raw": v, "type": classify(v)[0], "value": classify(v)[1]}
                                   for v in part_links.values()]})
            st.download_meta_json = meta
            st.download_status = "pending"; st.grade_status = "pending"
            repo.audit(s, "set_link", eval_id=ev.id, student_id=st.id,
                       after={"part_links": part_links})
            s.commit()
        return RedirectResponse(f"/evals/{slug}", status_code=303)

    link = (form.get("link") or "").strip()                # single-link (combined) evals
    if link:
        stype, val = classify(link)
        empty = stype == "empty"
        st.submission_raw = link
        st.submission_type = stype
        meta = dict(st.download_meta_json or {})
        meta.update({"mode": "none" if empty else "single", "n_links": 0 if empty else 1,
                     "n_questions": 1,
                     "links": [{"question_id": "", "raw": link, "type": stype, "value": val}]})
        st.download_meta_json = meta
        st.download_status = "not_submitted" if empty else "pending"
        st.grade_status = "not_submitted" if empty else "pending"
        repo.audit(s, "set_link", eval_id=ev.id, student_id=st.id, after={"link": link, "type": stype})
        s.commit()
    return RedirectResponse(f"/evals/{slug}", status_code=303)


@app.get("/evals/{slug}/results.csv")
def results_csv(slug: str, s: Session = Depends(get_db)):
    import csv
    import io
    from fastapi.responses import Response
    ev = _eval_or_404(s, slug)
    parts = repo.parts(s, ev.id)
    rows = _results_rows(s, ev, parts)
    cols = (["User Code", "Name", "Email", "Total_Score", "Total_Max", "Scaled", "Overall", "Remarks"]
            + [f"{p.key}_{x}" for p in parts for x in ("score", "max", "feedback")]
            + ["Report_URL"])
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols)
    w.writeheader()
    for r in rows:
        w.writerow(r)
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{slug}_grades.csv"'})


@app.post("/evals/{slug}/push-sheet")
def push_sheet(slug: str, s: Session = Depends(get_db)):
    ev = _eval_or_404(s, slug)
    # metabase evals: write grades INTO the existing submission tab (aligned to each
    # StudentId/Question_ID row). Only non-metabase evals fall back to a {slug}-grades tab.
    sub_tab = (ev.config_json or {}).get("submissions_tab")
    try:
        if sub_tab:
            from grader.metabase import write_grades_to_sheet
            res = write_grades_to_sheet(ev.slug)
        else:
            from grader.results_sheet import push_grades
            parts = repo.parts(s, ev.id)
            res = push_grades(ev.slug, _results_rows(s, ev, parts), parts)
    except Exception as e:
        return RedirectResponse(f"/evals/{slug}?push_error={type(e).__name__}: {e}",
                                status_code=303)
    cfg = dict(ev.config_json or {})
    cfg["grades_pushed"] = res
    ev.config_json = cfg
    s.commit()
    return RedirectResponse(f"/evals/{slug}?pushed=1", status_code=303)


# ── rubric + problem-statement setup (draft → setup) ─────────────────────────
def _pending_meta(ev) -> dict:
    return (ev.config_json or {}).get("pending", {})


def _wt(ev) -> dict:
    """Weightage as a first-class data point for any page showing this eval:
    the raw string, the posture tier (light/moderate/heavy), and the deadline."""
    from grader import rubric_ai
    p = _pending_meta(ev)
    w = p.get("weightage", "")
    t = rubric_ai.weightage_tier(w)
    return {"weightage": w, "tier": t,
            "deadline": p.get("grading_deadline") or p.get("effective_deadline", "")}


def _setup_ctx(request, ev, **extra):
    from grader import rubric_ai
    from grader.pending_sheet import SHEET_ID
    p = _pending_meta(ev)
    return {"request": request, "ev": ev, "pending": p, "wt": _wt(ev),
            "n_parts": len(ev.parts), "ds": (ev.config_json or {}).get("dataset", {}),
            "atom": (ev.config_json or {}).get("atomized", {}),
            "sheet_id": SHEET_ID,
            "has_sheet": bool((ev.config_json or {}).get("submissions_tab")),
            "tier": rubric_ai.weightage_tier(p.get("weightage", "")), **extra}


def _atom_marks_warning(sections) -> str:
    """Non-blocking marks-consistency check for a (hand-edited) atomised rubric:
    atoms should sum to their criterion, criteria to the section max."""
    bad = []
    for sec in sections:
        csum = round(sum(c.get("marks", 0) for c in sec.get("criteria", [])), 2)
        if abs(csum - sec.get("max", 0)) > 1e-6:
            bad.append(f"{sec.get('title', '?')}: criteria sum {csum:g} ≠ section max {sec.get('max', 0):g}")
        for c in sec.get("criteria", []):
            asum = round(sum(a.get("marks", 0) for a in c.get("atoms", [])), 2)
            if c.get("atoms") and abs(asum - c.get("marks", 0)) > 1e-6:
                bad.append(f"“{c.get('criterion', '?')[:40]}”: atoms sum {asum:g} ≠ {c.get('marks', 0):g}")
    return "" if not bad else "Marks no longer balance — " + "; ".join(bad[:6])


@app.get("/evals/{slug}/setup", response_class=HTMLResponse)
def setup_form(request: Request, slug: str, s: Session = Depends(get_db)):
    ev = _eval_or_404(s, slug)
    return render(request, "setup.html", _setup_ctx(request, ev, error=None))


@app.post("/evals/{slug}/setup/save-rubric")
async def setup_save_rubric(request: Request, slug: str, s: Session = Depends(get_db)):
    """Save the rubric + paper in place (no redirect) so the reviewer can then
    atomise. Mirrors the full save: parses parts, clears a stale atomised rubric."""
    from app.db.models import Part
    from grader.intake import parse_rubric
    ev = _eval_or_404(s, slug)
    form = await request.form()
    rubric = (form.get("rubric_markdown") or "").strip()
    ps = (form.get("problem_statement") or "").strip()
    total = (form.get("total_marks") or "").strip()
    normalize = (form.get("normalize_to") or "10").strip()
    part_defs = parse_rubric(rubric)
    if not part_defs:
        return JSONResponse({"error": "No rubric sections parsed — use "
                             "‘## S1 — Title (marks)’ headers with ‘- criterion — marks’ bullets."})
    if rubric != (ev.rubric_md or "").strip():
        cfg = dict(ev.config_json or {})
        if cfg.pop("atomized", None) is not None:
            ev.config_json = cfg
    ev.rubric_md = rubric
    ev.problem_statement_md = ps
    ev.total_marks = int(total) if total else int(round(sum(p["max_marks"] for p in part_defs)))
    ev.normalize_to = int(normalize) if normalize else 10
    if ev.status == "draft":
        ev.status = "setup"
    for old in repo.parts(s, ev.id):
        s.delete(old)
    s.flush()
    for p in part_defs:
        s.add(Part(eval_id=ev.id, key=p["key"], title=p["title"],
                   max_marks=p["max_marks"], order=p["order"], breakdown_json=p["breakdown"]))
    s.commit()
    return JSONResponse({"ok": True, "n_parts": len(part_defs), "total": ev.total_marks})


@app.post("/evals/{slug}/setup/analyze")
async def setup_analyze(request: Request, slug: str, s: Session = Depends(get_db)):
    _eval_or_404(s, slug)
    from grader import rubric_ai
    form = await request.form()
    try:
        return JSONResponse(rubric_ai.analyze_paper(form.get("problem_statement", "")))
    except Exception as e:
        return JSONResponse({"error": f"{type(e).__name__}: {e}"})


@app.post("/evals/{slug}/setup/extract")
async def setup_extract(request: Request, slug: str, s: Session = Depends(get_db)):
    """Lift the paper's OWN rubric verbatim — the fair default when the paper
    already ships a mark scheme that was shared with students."""
    _eval_or_404(s, slug)
    from grader import rubric_ai
    form = await request.form()
    try:
        return JSONResponse(rubric_ai.extract_rubric(form.get("problem_statement", "")))
    except Exception as e:
        return JSONResponse({"error": f"{type(e).__name__}: {e}"})


@app.post("/evals/{slug}/setup/dataset/detect")
async def dataset_detect(request: Request, slug: str, s: Session = Depends(get_db)):
    """Find candidate dataset links in the pasted problem statement."""
    _eval_or_404(s, slug)
    from grader import dataset
    form = await request.form()
    try:
        return JSONResponse({"candidates": dataset.detect_links(form.get("problem_statement", ""))})
    except Exception as e:
        return JSONResponse({"error": f"{type(e).__name__}: {e}"})


@app.post("/evals/{slug}/setup/dataset/build")
async def dataset_build(request: Request, slug: str, s: Session = Depends(get_db)):
    """Download the confirmed dataset link(s) once, profile them, store the brief."""
    ev = _eval_or_404(s, slug)
    from grader import dataset
    form = await request.form()
    links = [l.strip() for l in form.getlist("links") if l.strip()]
    try:
        return JSONResponse(dataset.ingest(ev.id, links))
    except Exception as e:
        return JSONResponse({"error": f"{type(e).__name__}: {e}"})


@app.post("/evals/{slug}/setup/dataset/reference")
async def dataset_reference(request: Request, slug: str, s: Session = Depends(get_db)):
    """Draft reviewable reference findings from the stored brief + paper + rubric."""
    ev = _eval_or_404(s, slug)
    from grader import dataset
    ds = (ev.config_json or {}).get("dataset", {})
    if not ds.get("brief"):
        return JSONResponse({"error": "Profile the dataset first."})
    try:
        return JSONResponse({"reference_md": dataset.reference_findings(
            ds["brief"], ev.problem_statement_md or "", ev.rubric_md or "")})
    except Exception as e:
        return JSONResponse({"error": f"{type(e).__name__}: {e}"})


@app.post("/evals/{slug}/setup/dataset/save-reference")
async def dataset_save_reference(request: Request, slug: str, s: Session = Depends(get_db)):
    """Persist the user-reviewed reference findings + whether they enter grading."""
    ev = _eval_or_404(s, slug)
    from grader import dataset
    form = await request.form()
    approved = (form.get("approved") or "").strip() in ("1", "true", "on", "yes")
    try:
        dataset.save_reference(ev.id, form.get("reference_md", ""), approved)
        return JSONResponse({"ok": True, "approved": approved})
    except Exception as e:
        return JSONResponse({"error": f"{type(e).__name__}: {e}"})


@app.post("/evals/{slug}/setup/atomize")
async def setup_atomize(request: Request, slug: str, s: Session = Depends(get_db)):
    """Decompose the saved rubric into granular checkable atoms (grader-internal,
    marks preserved). Stores an UNapproved draft; user reviews then approves."""
    ev = _eval_or_404(s, slug)
    from grader import rubric_ai
    form = await request.form()
    if not (ev.rubric_md or "").strip():
        return JSONResponse({"error": "Save the rubric first, then atomise it."})
    posted = (form.get("rubric_markdown") or "").strip()
    if posted and posted != (ev.rubric_md or "").strip():
        return JSONResponse({"error": "You have unsaved rubric edits. Click "
                             "‘Save rubric & continue’ first, then atomise the saved rubric."})
    ds = (ev.config_json or {}).get("dataset", {})
    try:
        res = rubric_ai.atomize_rubric(ev.rubric_md, ev.problem_statement_md or "",
                                       ds.get("brief", ""))
    except Exception as e:
        return JSONResponse({"error": f"{type(e).__name__}: {e}"})
    cfg = dict(ev.config_json or {})
    cfg["atomized"] = {"sections": res["sections"], "total": res["total"],
                       "notes": res.get("notes", ""), "markdown": res["markdown"],
                       "approved": False}
    ev.config_json = cfg
    s.commit()
    return JSONResponse({"markdown": res["markdown"], "total": res["total"],
                         "notes": res.get("notes", "")})


@app.post("/evals/{slug}/setup/atomize/approve")
async def setup_atomize_approve(request: Request, slug: str, s: Session = Depends(get_db)):
    """Persist the (possibly hand-edited) atomised rubric and toggle whether it is
    used in grading. If the reviewer edited the markdown, it is re-parsed back into
    the structured form the grader consumes — so edits actually change grading."""
    ev = _eval_or_404(s, slug)
    from grader import rubric_ai
    form = await request.form()
    approved = (form.get("approved") or "").strip() in ("1", "true", "on", "yes")
    cfg = dict(ev.config_json or {})
    atom = dict(cfg.get("atomized", {}))
    edited = (form.get("markdown") or "").strip()
    if edited:
        parsed = rubric_ai.parse_atoms_md(edited)
        if not parsed["sections"]:
            return JSONResponse({"error": "Could not parse the atomised rubric — keep the "
                                 "‘## Section (max)’, ‘- criterion — marks’, ‘• atom — marks’ structure."})
        atom["sections"] = parsed["sections"]
        atom["total"] = parsed["total"]
        atom["markdown"] = rubric_ai._atoms_md(parsed["sections"])   # normalise display
    if not atom.get("sections"):
        return JSONResponse({"error": "Atomise the rubric first."})
    # marks sanity: warn (do not block) if atoms/criteria no longer sum cleanly
    warn = _atom_marks_warning(atom["sections"])
    atom["approved"] = approved
    cfg["atomized"] = atom
    ev.config_json = cfg
    s.commit()
    return JSONResponse({"ok": True, "approved": approved, "total": atom.get("total"),
                         "markdown": atom.get("markdown", ""), "warning": warn})


@app.post("/evals/{slug}/setup/reset")
async def setup_reset(request: Request, slug: str, s: Session = Depends(get_db)):
    """Clear a setup field back to empty. `field` ∈ {problem, rubric, atomized,
    dataset, all}. Resetting the rubric also drops its parts and atomised rubric
    (they belong to it); resetting the paper leaves the rubric alone."""
    ev = _eval_or_404(s, slug)
    form = await request.form()
    field = (form.get("field") or "").strip()
    cfg = dict(ev.config_json or {})
    if field in ("problem", "all"):
        ev.problem_statement_md = ""
    if field in ("rubric", "all"):
        ev.rubric_md = ""
        for old in repo.parts(s, ev.id):
            s.delete(old)
        cfg.pop("atomized", None)                       # atomised belongs to the rubric
    if field in ("atomized", "all"):
        cfg.pop("atomized", None)
    if field in ("dataset", "all"):
        cfg.pop("dataset", None)
    ev.config_json = cfg
    repo.audit(s, "setup_reset", eval_id=ev.id, after={"field": field})
    s.commit()
    return JSONResponse({"ok": True, "field": field})


@app.post("/evals/{slug}/setup/generate")
async def setup_generate(request: Request, slug: str, s: Session = Depends(get_db)):
    ev = _eval_or_404(s, slug)
    from grader import rubric_ai
    form = await request.form()
    total = (form.get("total_marks") or "").strip()
    try:
        res = rubric_ai.generate_rubric(form.get("problem_statement", ""),
                                        int(total) if total else 100,
                                        _pending_meta(ev).get("weightage", ""))
        return JSONResponse(res)
    except Exception as e:
        return JSONResponse({"error": f"{type(e).__name__}: {e}"})


@app.post("/evals/{slug}/setup")
async def setup_save(request: Request, slug: str, s: Session = Depends(get_db)):
    from app.db.models import Part
    from grader.intake import parse_rubric
    ev = _eval_or_404(s, slug)
    form = await request.form()
    ps = (form.get("problem_statement") or "").strip()
    rubric = (form.get("rubric_markdown") or "").strip()
    total = (form.get("total_marks") or "").strip()
    normalize = (form.get("normalize_to") or "10").strip()
    part_defs = parse_rubric(rubric)
    if not part_defs:
        return render(request, "setup.html", _setup_ctx(request, ev,
            error="No rubric sections parsed — use '## S1 — Title (marks)' "
                  "headers with '- criterion — marks' bullets, or click Generate."))
    tmax = int(total) if total else int(round(sum(p["max_marks"] for p in part_defs)))
    if rubric != (ev.rubric_md or "").strip():
        cfg = dict(ev.config_json or {})
        if cfg.pop("atomized", None) is not None:      # atomised belongs to the OLD
            ev.config_json = cfg                        # rubric — drop it so it can't
    ev.rubric_md = rubric                               # go stale against a new rubric
    ev.problem_statement_md = ps
    ev.total_marks = tmax
    ev.normalize_to = int(normalize) if normalize else 10
    ev.status = "setup"
    for old in repo.parts(s, ev.id):                       # replace on re-save
        s.delete(old)
    s.flush()
    for p in part_defs:
        s.add(Part(eval_id=ev.id, key=p["key"], title=p["title"],
                   max_marks=p["max_marks"], order=p["order"],
                   breakdown_json=p["breakdown"]))
    repo.audit(s, "rubric_setup", eval_id=ev.id,
               after={"parts": len(part_defs), "total_marks": tmax})
    s.commit()
    return RedirectResponse(f"/evals/{slug}", status_code=303)


@app.post("/evals/{slug}/preview")
def run_preview(slug: str, s: Session = Depends(get_db)):
    from grader.preview import preview_and_recommend
    ev = _eval_or_404(s, slug)
    runner.start_job(ev.id, "preview", lambda p: preview_and_recommend(ev.id, progress=p))
    return RedirectResponse(f"/evals/{slug}", status_code=303)


@app.post("/evals/{slug}/run/{step}")
def run_step(slug: str, step: str, next: str = "", regrade: str = "", n: str = "",
             model: str = Form(""), s: Session = Depends(get_db)):
    ev = _eval_or_404(s, slug)
    eid = ev.id
    if step == "download":
        from grader.download import download_eval
        runner.start_job(eid, "download", lambda p: download_eval(eid, progress=p))
    elif step == "grade":
        from grader.grade import grade_eval
        only_pending = regrade != "1"                 # ?regrade=1 → re-grade everyone
        # policy: a fresh grade-all / grade-pending is single pass (n=1, deterministic);
        # a regrade is median-of-3 for dispute assurance. An explicit ?n= overrides.
        nn = int(n) if n.strip().isdigit() else (3 if regrade == "1" else 1)
        runner.clear_stop(eid)                        # fresh run: not pre-cancelled
        runner.start_job(eid, "grade",
                         lambda p: grade_eval(eid, progress=p, model=model or None,
                                              only_pending=only_pending, ensemble_n=nn,
                                              should_stop=lambda: runner.stop_requested(eid)))
    elif step == "report":
        from grader.report import report_eval
        runner.start_job(eid, "report", lambda p: report_eval(eid, progress=p))
    dest = f"/evals/{slug}/submissions" if next == "submissions" else f"/evals/{slug}"
    return RedirectResponse(dest, status_code=303)


@app.post("/evals/{slug}/stop-grading")
def stop_grading(slug: str, s: Session = Depends(get_db)):
    """Cooperatively stop a running grade run: students already in flight finish,
    every queued student is skipped (never added to the queue)."""
    ev = _eval_or_404(s, slug)
    runner.request_stop(ev.id)
    return RedirectResponse(f"/evals/{slug}", status_code=303)


@app.post("/evals/{slug}/job/clear")
def clear_job_log(slug: str, s: Session = Depends(get_db)):
    """Reset the download/grade/report log window."""
    ev = _eval_or_404(s, slug)
    runner.clear_log(ev.id)
    return RedirectResponse(f"/evals/{slug}", status_code=303)


@app.get("/evals/{slug}/job", response_class=HTMLResponse)
def job_status(request: Request, slug: str, s: Session = Depends(get_db)):
    ev = _eval_or_404(s, slug)
    job = runner.latest_job(ev.id)
    return render(request, "_job.html",
        {"request": request, "job": job, "summary": _summary(s, ev), "ev": ev})


# ── report cards (S3 URL if present, else served from disk) ──────────────────
@app.get("/evals/{slug}/card/{code}")
def report_card(slug: str, code: str, s: Session = Depends(get_db)):
    import os
    ev = _eval_or_404(s, slug)
    st = repo.get_student(s, ev.id, code)
    if not st:
        raise HTTPException(404, f"no student '{code}'")
    rep = (st.download_meta_json or {}).get("report", {})
    if rep.get("url"):                                    # S3 (or any hosted) URL wins
        return RedirectResponse(rep["url"])
    path = rep.get("card_file")
    if path and os.path.exists(path):
        return FileResponse(path, media_type="application/pdf",
                            filename=f"{code}.pdf")
    raise HTTPException(404, "no report card for this student")


# ── per-student regrade / regenerate card (dispute handling) ─────────────────
@app.post("/evals/{slug}/student/{code}/regrade")
def regrade_student(slug: str, code: str, s: Session = Depends(get_db)):
    ev = _eval_or_404(s, slug)
    eid = ev.id

    def _job(p):
        from grader.grade import grade_student
        from grader.report import report_one
        grade_student(eid, code, progress=p)
        report_one(eid, code, progress=p)          # regenerate that card, same S3 link
        return {"regraded": code}

    runner.start_job(eid, f"regrade {code}", _job)
    return RedirectResponse(f"/evals/{slug}", status_code=303)


@app.post("/evals/{slug}/student/{code}/regen-card")
def regen_card(slug: str, code: str, s: Session = Depends(get_db)):
    ev = _eval_or_404(s, slug)
    from grader.report import report_one
    runner.start_job(ev.id, f"card {code}", lambda p: report_one(ev.id, code, progress=p))
    return RedirectResponse(f"/evals/{slug}", status_code=303)


# ── submissions (loaded from a tab in the master sheet) ──────────────────────
@app.get("/evals/{slug}/submissions", response_class=HTMLResponse)
def submissions(request: Request, slug: str, s: Session = Depends(get_db)):
    from grader.pending_sheet import SHEET_ID
    ev = _eval_or_404(s, slug)
    studs = repo.students(s, ev.id)
    modes = collections.Counter((st.download_meta_json or {}).get("mode", "?") for st in studs)
    missing = sum(1 for st in studs if st.submission_type != "empty"
                  and (not st.name or not st.email))
    n_submitted = len(studs) - modes.get("none", 0)
    dl = collections.Counter(st.download_status for st in studs if st.submission_type != "empty")
    return render(request, "submissions.html",
        {"request": request, "ev": ev, "students": studs,
         "wt": _wt(ev), "n_parts": len(repo.parts(s, ev.id)),
         "modes": dict(modes), "missing": missing, "n_submitted": n_submitted,
         "dl": dict(dl), "n_downloaded": dl.get("ok", 0),
         "job": runner.latest_job(ev.id), "summary": _summary(s, ev),
         "tab": (ev.config_json or {}).get("submissions_tab", ev.slug),
         "sheet_id": SHEET_ID})


@app.post("/evals/{slug}/metabase/create")
def metabase_create(slug: str, s: Session = Depends(get_db)):
    """Fetch this assignment from Metabase → write the submission tab (= slug) +
    auto-fill the eval's questions & rubrics. Runs as a job (blocking API calls)."""
    from grader.metabase import create_submission_sheet
    ev = _eval_or_404(s, slug)
    runner.start_job(ev.id, "metabase-sheet", lambda p: create_submission_sheet(slug, progress=p))
    return RedirectResponse(f"/evals/{slug}/submissions", status_code=303)


@app.post("/evals/{slug}/metabase/release")
def metabase_release(slug: str, s: Session = Depends(get_db)):
    """One eval, one sheet: write the grading columns (Score, MaxMarks, answerScore,
    Feedback) into the SAME submission tab. Run after grading."""
    from grader.metabase import write_grades_to_sheet
    ev = _eval_or_404(s, slug)
    runner.start_job(ev.id, "metabase-release", lambda p: write_grades_to_sheet(slug))
    return RedirectResponse(f"/evals/{slug}/submissions", status_code=303)


@app.post("/evals/{slug}/metabase/qc")
async def metabase_qc(slug: str, s: Session = Depends(get_db)):
    """Validate the written submission tab before grading. Threadpool = no event-loop block."""
    from starlette.concurrency import run_in_threadpool
    from grader.metabase import qc_submission_sheet
    _eval_or_404(s, slug)
    try:
        return JSONResponse(await run_in_threadpool(qc_submission_sheet, slug))
    except Exception as e:
        return JSONResponse({"error": f"{type(e).__name__}: {e}"})


@app.post("/evals/{slug}/submissions/fetch")
async def submissions_fetch(request: Request, slug: str, s: Session = Depends(get_db)):
    from grader.submissions_sheet import load_submissions
    ev = _eval_or_404(s, slug)
    form = await request.form()
    tab = (form.get("tab") or ev.slug).strip()
    try:
        res = load_submissions(ev.id, tab)
    except Exception as e:
        return RedirectResponse(
            f"/evals/{slug}/submissions?error={type(e).__name__}: {e}", status_code=303)
    return RedirectResponse(
        f"/evals/{slug}/submissions?added={res['added']}&updated={res['updated']}",
        status_code=303)


@app.post("/evals/{slug}/submissions/clear")
async def submissions_clear(slug: str, s: Session = Depends(get_db)):
    """Delete ALL student rows for this eval — for a clean re-fetch (e.g. after a
    column-mapping fix left stale/duplicate rows). Refuses if any are graded."""
    from app.db.models import Student
    ev = _eval_or_404(s, slug)
    graded = sum(1 for st in repo.students(s, ev.id) if st.grade_status == "graded")
    if graded:
        return RedirectResponse(
            f"/evals/{slug}/submissions?error=Refusing to clear — {graded} student(s) are "
            f"already graded. Regrade individually instead.", status_code=303)
    n = s.query(Student).filter_by(eval_id=ev.id).delete()
    repo.audit(s, "clear_submissions", eval_id=ev.id, after={"deleted": n})
    s.commit()
    return RedirectResponse(f"/evals/{slug}/submissions?cleared={n}", status_code=303)


@app.get("/evals/{slug}/submissions/download-qc")
def submissions_download_qc(slug: str, s: Session = Depends(get_db)):
    """Read-only cross-check of what actually downloaded (empty/failed links, ok
    links with 0 files on disk). Never re-downloads."""
    from grader.download import download_qc
    ev = _eval_or_404(s, slug)
    return JSONResponse(download_qc(ev.id))


@app.post("/evals/{slug}/submissions/download-qc/pass/{code}")
def submissions_qc_pass(slug: str, code: str, s: Session = Depends(get_db)):
    """Manually mark a QC'd student's download as verified-OK so grading includes
    them — for an acceptable 'partial' (e.g. one intentionally-empty repo). Does
    not re-download; only flips the status + records who approved it."""
    ev = _eval_or_404(s, slug)
    st = repo.get_student(s, ev.id, code)
    if not st:
        return JSONResponse({"error": "student not found"})
    before = st.download_status
    st.download_status = "ok"
    meta = dict(st.download_meta_json or {})
    dl = dict(meta.get("download", {})); dl["qc_approved"] = True
    meta["download"] = dl
    st.download_meta_json = meta
    repo.audit(s, "download_qc_pass", eval_id=ev.id, student_id=st.id,
               before={"download_status": before},
               after={"download_status": "ok", "qc_approved": True})
    s.commit()
    return JSONResponse({"ok": True, "code": code})


# ── QC ──────────────────────────────────────────────────────────────────────
def _qc_stats(ev, studs, parts, gmap) -> dict:
    """Distribution, summary, per-section means, and attention flags for QC."""
    import statistics as _st
    scores = [x.total_score or 0 for x in studs]
    tm = ev.total_marks or 1
    hist = [0] * 10                                    # 10 pct bands
    for v in scores:
        hist[min(9, int(v / tm * 10))] += 1
    # per-section mean (% of that section's max)
    sec = []
    for p in parts:
        vals = [gmap.get(x.id, {}).get(p.key).score for x in studs
                if gmap.get(x.id, {}).get(p.key)]
        mean = round(sum(vals) / len(vals), 1) if vals else 0
        sec.append({"key": p.key.split("_")[0], "title": p.title, "max": p.max_marks,
                    "mean": mean, "pct": round(mean / p.max_marks * 100) if p.max_marks else 0})
    dbls = [(x.download_meta_json or {}).get("double") for x in studs]
    return {
        "n": len(studs),
        "mean": round(_st.mean(scores), 1) if scores else 0,
        "median": round(_st.median(scores), 1) if scores else 0,
        "std": round(_st.pstdev(scores), 1) if len(scores) > 1 else 0,
        "min": min(scores) if scores else 0, "max": max(scores) if scores else 0,
        "hist": hist, "hist_max": max(hist) if hist else 1, "sections": sec,
        "zeros": sum(1 for v in scores if v == 0),
        "n_double": sum(1 for d in dbls if d),
        "n_flagged": sum(1 for d in dbls if d and d.get("flagged")),
    }


@app.get("/evals/{slug}/qc", response_class=HTMLResponse)
def qc(request: Request, slug: str, sort: str = "score", filter: str = "all",
       s: Session = Depends(get_db)):
    ev = _eval_or_404(s, slug)
    studs = [st for st in repo.students(s, ev.id) if st.grade_status == "graded"]
    parts = repo.parts(s, ev.id)
    gmap = {}
    for g in repo.all_grades(s, ev.id):
        gmap.setdefault(g.student_id, {})[g.part_key] = g
    stats = _qc_stats(ev, studs, parts, gmap)
    if filter == "flagged":
        studs = [x for x in studs if (x.download_meta_json or {}).get("double", {}).get("flagged")]
    elif filter == "zeros":
        studs = [x for x in studs if (x.total_score or 0) == 0]
    elif filter == "undouble":
        studs = [x for x in studs if not (x.download_meta_json or {}).get("double")]
    studs.sort(key=(lambda st: st.total_score or 0) if sort == "score" else (lambda st: st.student_code))
    return render(request, "qc.html",
        {"request": request, "ev": ev, "students": studs, "sort": sort,
         "filter": filter, "stats": stats,
         "wt": _wt(ev), "n_parts": len(repo.parts(s, ev.id)),
         "job": runner.latest_job(ev.id), "summary": _summary(s, ev)})


@app.post("/evals/{slug}/student/{code}/double-grade")
def double_grade_one(slug: str, code: str, s: Session = Depends(get_db)):
    ev = _eval_or_404(s, slug)
    from grader.grade import double_grade_student
    runner.start_job(ev.id, f"double {code}", lambda p: double_grade_student(ev.id, code, progress=p))
    return RedirectResponse(f"/evals/{slug}/qc", status_code=303)


@app.post("/evals/{slug}/qc/double-grade-all")
def double_grade_bulk(slug: str, s: Session = Depends(get_db)):
    ev = _eval_or_404(s, slug)
    from grader.grade import double_grade_eval
    runner.start_job(ev.id, "double-grade all", lambda p: double_grade_eval(ev.id, progress=p))
    return RedirectResponse(f"/evals/{slug}/qc", status_code=303)


@app.post("/evals/{slug}/student/{code}/adopt-double")
def adopt_double(slug: str, code: str, s: Session = Depends(get_db)):
    """Promote the second-pass scores to primary, then regenerate the card."""
    ev = _eval_or_404(s, slug)
    st = repo.get_student(s, ev.id, code)
    dbl = (st.download_meta_json or {}).get("double")
    if dbl:
        before = {g.part_key: g.score for g in repo.grades_for(s, st.id)}
        total = 0.0
        for p in repo.parts(s, ev.id):
            g = repo.upsert_grade(s, st.id, p.key)
            g.score = float(dbl["sections"].get(p.key, g.score))
            total += g.score
        st.total_score = round(total, 2)
        st.overall_feedback = dbl.get("overall") or st.overall_feedback
        meta = dict(st.download_meta_json or {}); meta.pop("double", None)
        st.download_meta_json = meta
        repo.audit(s, "adopt_double", eval_id=ev.id, student_id=st.id, actor="user",
                   before=before, after={p: dbl["sections"].get(p) for p in dbl["sections"]})
        s.commit()
        from grader.report import report_one
        runner.start_job(ev.id, f"card {code}", lambda p: report_one(ev.id, code, progress=p))
    return RedirectResponse(f"/evals/{slug}/qc", status_code=303)


@app.get("/evals/{slug}/student/{code}", response_class=HTMLResponse)
def student_detail(request: Request, slug: str, code: str, s: Session = Depends(get_db)):
    ev = _eval_or_404(s, slug)
    st = repo.get_student(s, ev.id, code)
    parts = repo.parts(s, ev.id)
    gmap = {g.part_key: g for g in repo.grades_for(s, st.id)}
    rows = [{"part": p, "grade": gmap.get(p.key)} for p in parts]
    return render(request, "_student.html",
        {"request": request, "ev": ev, "st": st, "rows": rows})


@app.post("/evals/{slug}/student/{code}/edit")
async def student_edit(slug: str, code: str, request: Request, s: Session = Depends(get_db)):
    from grader.report import _branding, _part_defs
    from grader.report_pdf import generate_pdf
    ev = _eval_or_404(s, slug)
    st = repo.get_student(s, ev.id, code)
    parts = repo.parts(s, ev.id)
    form = await request.form()
    before = {g.part_key: g.score for g in repo.grades_for(s, st.id)}
    total = 0.0
    for p in parts:
        g = repo.upsert_grade(s, st.id, p.key)
        ns, nf = form.get(f"score_{p.key}"), form.get(f"fb_{p.key}")
        if ns is not None:
            g.score = max(0.0, min(float(ns or 0), float(p.max_marks)))
        if nf is not None:
            g.feedback = nf
        total += g.score
    st.total_score = round(total, 2)
    if form.get("overall") is not None:
        st.overall_feedback = form.get("overall")
    repo.audit(s, "qc_edit", eval_id=ev.id, student_id=st.id, actor="user",
               before=before, after={g.part_key: g.score for g in repo.grades_for(s, st.id)})
    s.commit()
    cards_dir = eval_data_dir(ev.slug) / "cards"; cards_dir.mkdir(exist_ok=True)
    gmap = {g.part_key: g for g in repo.grades_for(s, st.id)}
    row = {"User Code": st.student_code, "Email": st.email,
           "Total_Score": st.total_score, "Total_Max": ev.total_marks}
    for p in parts:
        g = gmap.get(p.key)
        row[f"{p.key}_score"] = g.score if g else 0
        row[f"{p.key}_max"] = p.max_marks
        row[f"{p.key}_feedback"] = g.feedback if g else ""
    generate_pdf(row, cards_dir / f"{safe_filename(st.student_code)}.pdf", cards_dir,
                 _part_defs(parts), _branding(ev), ev.total_marks, ev.normalize_to)
    return RedirectResponse(f"/evals/{slug}/qc", status_code=303)


# ── distribute ──────────────────────────────────────────────────────────────
@app.get("/evals/{slug}/distribute", response_class=HTMLResponse)
def distribute(request: Request, slug: str, s: Session = Depends(get_db)):
    from grader.report import final_status
    ev = _eval_or_404(s, slug)
    rows = final_status(ev.id)
    cards_dir = eval_data_dir(ev.slug) / "cards"
    n_cards = len(list(cards_dir.glob("*.pdf"))) if cards_dir.exists() else 0
    return render(request, "distribute.html",
        {"request": request, "ev": ev, "rows": rows, "n_cards": n_cards,
         "wt": _wt(ev), "n_parts": len(repo.parts(s, ev.id)),
         "tally": dict(collections.Counter(r["Status"] for r in rows)), "cards_dir": str(cards_dir)})


# ── this week's pending (from the Outcomes tracker sheet) ────────────────────
@app.get("/pending", response_class=HTMLResponse)
def pending(request: Request, s: Session = Depends(get_db)):
    from grader import pending_sheet, rubric_ai
    today = datetime.date.today()
    ctx = {"request": request, "today": today,
           "week_end": pending_sheet._week_end(today),
           "sheet_id": pending_sheet.SHEET_ID, "picks": [], "error": None}
    try:
        picks = pending_sheet.pick_week(pending_sheet.read_rows(), today)
        existing = {ev.slug for ev in repo.list_evals(s)}
        for p in picks:
            p["imported"] = p["slug"] in existing
            p["tier"] = rubric_ai.weightage_tier(p.get("weightage", ""))
        ctx["picks"] = picks
        ctx["n_overdue"] = sum(1 for p in picks if p["overdue"])
    except Exception as e:
        ctx["error"] = f"{type(e).__name__}: {e}"
    return render(request, "pending.html", ctx)


@app.post("/pending/fetch")
def pending_fetch(s: Session = Depends(get_db)):
    from grader import pending_sheet
    from app.db.session import get_session

    def _run(log):
        log("5|reading tracker sheet…")
        rows = pending_sheet.read_rows(fresh=True)     # force a live read, not the page-view cache
        picks = pending_sheet.pick_week(rows, datetime.date.today())
        log(f"20|{len(picks)} pick(s) due this week — importing…")
        res = pending_sheet.import_picks(picks, progress=log)
        with get_session() as s2:
            repo.audit(s2, "pending_fetch", after=res); s2.commit()
        return res

    jid = runner.start_job(None, "pending-fetch", _run)
    return RedirectResponse(f"/pending?job={jid}", status_code=303)


@app.get("/pending/job", response_class=HTMLResponse)
def pending_job(request: Request):
    job = runner.latest_job_by_kind("pending-fetch")
    return render(request, "_pending_job.html", {"request": request, "job": job})


# ── tickets (Phase 3 placeholder) ───────────────────────────────────────────
def _ticket_file(slug: str, code: str) -> Path:
    return eval_data_dir(slug) / "tickets" / f"{safe_filename(code)}.json"


@app.get("/tickets", response_class=HTMLResponse)
def tickets(request: Request, slug: str = "", code: str = "", s: Session = Depends(get_db)):
    import json as _json
    evals = [e.slug for e in repo.list_evals(s) if e.status in ("graded", "finalized")]
    proposal = None
    if slug and code and _ticket_file(slug, code).exists():
        proposal = _json.loads(_ticket_file(slug, code).read_text())
    return render(request, "tickets.html", {"request": request, "evals": evals,
                                            "proposal": proposal, "slug": slug, "code": code})


@app.get("/tickets/student-info", response_class=HTMLResponse)
def ticket_student_info(request: Request, slug: str, code: str, s: Session = Depends(get_db)):
    """Fragment: a student's current score, report card + submission link, and per-section
    feedback — shown in the ticket form so you can see the existing grade before regrading."""
    ev = repo.get_eval(s, slug)
    if not ev:
        return HTMLResponse('<div class="flash">Eval not found.</div>')
    st = repo.get_student(s, ev.id, (code or "").strip())
    if not st:
        return HTMLResponse(f'<div class="flash">No student <b>{code}</b> in {slug}.</div>')
    parts = repo.parts(s, ev.id)
    gmap = {g.part_key: g for g in repo.grades_for(s, st.id)}
    meta = st.download_meta_json or {}
    pl, lk = meta.get("part_links"), (meta.get("links") or [])
    if pl:
        links = [{"key": k, "url": v} for k, v in pl.items()]
    elif lk:
        links = [{"key": "", "url": l.get("raw")} for l in lk if l.get("raw")]
    elif st.submission_raw and st.submission_raw.startswith("http"):
        links = [{"key": "", "url": st.submission_raw}]
    else:
        links = []
    return render(request, "_student_info.html",
        {"request": request, "ev": ev, "st": st, "parts": parts, "gmap": gmap,
         "rep": meta.get("report", {}), "links": links})


@app.post("/tickets/analyze")
async def tickets_analyze(request: Request):
    import json as _json
    from grader import ticket
    form = await request.form()
    slug = (form.get("slug") or "").strip(); code = (form.get("student_code") or "").strip()
    message = (form.get("message") or "").strip(); link = (form.get("link") or "").strip()
    # ticket.analyze is a blocking, multi-minute call (it regrades on the median
    # ensemble). Run it in a threadpool so it never blocks the event loop / freezes
    # the whole server for other requests while one analysis runs.
    from starlette.concurrency import run_in_threadpool
    res = await run_in_threadpool(ticket.analyze, slug, code, message, link)
    if res.get("error"):
        return RedirectResponse(f"/tickets?slug={slug}&code={code}&err={res['error'][:120]}", status_code=303)
    f = _ticket_file(slug, code); f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(_json.dumps(res, default=str))
    return RedirectResponse(f"/tickets?slug={slug}&code={code}", status_code=303)


@app.post("/tickets/apply")
async def tickets_apply(request: Request):
    import json as _json
    from grader import ticket
    form = await request.form()
    slug = (form.get("slug") or "").strip(); code = (form.get("student_code") or "").strip()
    f = _ticket_file(slug, code)
    if not f.exists():
        return RedirectResponse("/tickets", status_code=303)
    proposal = _json.loads(f.read_text())
    from starlette.concurrency import run_in_threadpool
    out = await run_in_threadpool(ticket.apply, slug, code, proposal)   # blocking: card + upload
    proposal["applied"] = out
    if out.get("draft_reply"):
        proposal["draft_reply"] = out["draft_reply"]
    f.write_text(_json.dumps(proposal, default=str))
    return RedirectResponse(f"/tickets?slug={slug}&code={code}", status_code=303)
