"""DB-backed report cards + final-status view. Reuses the existing ReportLab
engine (grader.report_pdf.generate_pdf) — we just source rows from the DB
instead of grading_master.csv, so cards are identical to the file-based flow."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Callable, Optional

from app.db import repo
from app.db.session import eval_data_dir, get_session, safe_filename
from grader.report_pdf import generate_pdf


def _part_defs(parts):
    """(key, label, short_title, max) — label is the leading code, short is the rest."""
    out = []
    for p in parts:
        m = re.match(r"^\s*([A-Za-z]{1,4}\d{1,3}|Part\s*\d+|Section\s*\d+)\s*[—:\-]?\s*(.*)$", p.title)
        label = (m.group(1) if m else p.key).strip()
        short = (m.group(2) if m and m.group(2) else p.title).strip()
        out.append((p.key, label, short, p.max_marks))
    return out


def _branding(ev):
    b = dict((ev.config_json or {}).get("branding", {}))
    b.setdefault("report_title", ev.title.split("—")[0].strip()[:24])
    b.setdefault("report_subtitle", ev.title)
    b.setdefault("badge_text", "GRADE REPORT")
    b.setdefault("footer_note", f"{ev.title} — Grading")
    b.setdefault("author", "GradePilot")
    b.setdefault("drive_folder", f"{ev.slug} — Graded")
    b.setdefault("sheet_title", f"{ev.title} — Grading Links")
    return b


def report_eval(eval_id: int, progress: Optional[Callable[[str], None]] = None) -> dict:
    log = progress or (lambda m: None)
    s = get_session()
    ev = repo.get_eval_by_id(s, eval_id)
    parts = repo.parts(s, eval_id)
    pdefs, branding = _part_defs(parts), _branding(ev)
    cards_dir = eval_data_dir(ev.slug) / "cards"; cards_dir.mkdir(exist_ok=True)

    graded = [st for st in repo.students(s, eval_id) if st.grade_status == "graded"]
    grades_by_student = {}                            # fetch once — no per-loop query (avoids holding a write lock)
    for g in repo.all_grades(s, eval_id):
        grades_by_student.setdefault(g.student_id, {})[g.part_key] = g
    n = len(graded)
    log(f"5|rendering {n} report cards")
    for i, st in enumerate(graded, 1):
        gmap = grades_by_student.get(st.id, {})
        row = {"User Code": st.student_code, "Name": st.name, "Email": st.email,
               "Total_Score": st.total_score or 0, "Total_Max": ev.total_marks}
        for p in parts:
            g = gmap.get(p.key)
            row[f"{p.key}_score"] = g.score if g else 0
            row[f"{p.key}_max"] = p.max_marks
            row[f"{p.key}_feedback"] = g.feedback if g else ""
        card = cards_dir / f"{safe_filename(st.student_code)}.pdf"
        generate_pdf(row, card, cards_dir, pdefs, branding, ev.total_marks, ev.normalize_to)
        meta = dict(st.download_meta_json or {})
        rep = dict(meta.get("report", {}))
        rep["card_file"] = str(card); rep["has_card"] = True
        meta["report"] = rep; st.download_meta_json = meta
        s.commit()                                    # per-student → releases the write lock between cards
        if i % 5 == 0 or i == n:
            log(f"{int(5 + i / n * 80)}|rendered {i}/{n} cards")

    # publish to S3 so students get a durable, viewable link (regrade-safe)
    from grader import storage
    if storage.enabled():
        log(f"88|uploading {n} cards to S3…")
        up = storage.backfill_cards(eval_id)
        log(f"96|uploaded {up['cards_uploaded']} cards to S3")
    else:
        log("88|S3 not configured — cards served from local disk")

    ev.status = "finalized"; s.commit()
    log(f"100|done · {n} report cards")
    return {"cards": n, "dir": str(cards_dir)}


def report_one(eval_id: int, code: str, progress: Optional[Callable[[str], None]] = None) -> dict:
    """(Re)render ONE student's card + re-upload to S3, REUSING the same S3 key
    so the shareable link stays valid after a regrade (overwrite in place)."""
    import secrets
    log = progress or (lambda m: None)
    s = get_session()
    ev = repo.get_eval_by_id(s, eval_id)
    parts = repo.parts(s, eval_id)
    pdefs, branding = _part_defs(parts), _branding(ev)
    cards_dir = eval_data_dir(ev.slug) / "cards"; cards_dir.mkdir(exist_ok=True)
    st = repo.get_student(s, eval_id, code)
    gmap = {g.part_key: g for g in repo.grades_for(s, st.id)}
    # For evals where `student_code` stores Metabase's numeric uid (not the real
    # text student code - an id_col auto-detection quirk on a handful of evals),
    # prefer the real code stashed in download_meta_json when present, so the
    # card shows the human-meaningful code rather than the internal uid.
    display_code = (st.download_meta_json or {}).get("metabase_user_code") or st.student_code
    row = {"User Code": display_code, "Name": st.name, "Email": st.email,
           "Total_Score": st.total_score or 0, "Total_Max": ev.total_marks}
    for p in parts:
        g = gmap.get(p.key)
        row[f"{p.key}_score"] = g.score if g else 0
        row[f"{p.key}_max"] = p.max_marks
        row[f"{p.key}_feedback"] = g.feedback if g else ""
    card = cards_dir / f"{safe_filename(code)}.pdf"
    generate_pdf(row, card, cards_dir, pdefs, branding, ev.total_marks, ev.normalize_to)
    meta = dict(st.download_meta_json or {}); rep = dict(meta.get("report", {}))
    rep["card_file"] = str(card); rep["has_card"] = True
    from grader import storage
    if storage.enabled():
        log(f"85|uploading {code} card to S3…")
        key = rep.get("s3_key") or storage.card_key(ev.slug, code, secrets.token_hex(4))
        rep["url"] = storage.upload(card, key)          # overwrite same key → same link
        rep["s3_key"] = key
    meta["report"] = rep; st.download_meta_json = meta; s.commit()
    log(f"100|regenerated card for {code}")
    return {"code": code, "url": rep.get("url")}


def final_status(eval_id: int) -> list[dict]:
    """Every student + final disposition — the ops view. Returns plain dicts."""
    s = get_session()
    try:
        return _final_status(s, eval_id)
    finally:
        s.close()


def _final_status(s, eval_id: int) -> list[dict]:
    ev = repo.get_eval_by_id(s, eval_id)
    NOTE = {"restricted": "Not shared publicly — ask student to re-share 'anyone with link'",
            "not_gradeable": "Non-Drive host / empty — needs manual review",
            "failed": "Download failed — retry or manual review",
            "not_submitted": "No submission provided"}
    rows = []
    for st in repo.students(s, eval_id):
        if st.grade_status == "graded":
            status, note, tot = "graded", "", st.total_score
            scaled = round((tot or 0) / ev.total_marks * ev.normalize_to, 1)
        else:
            status = st.download_status if st.download_status != "ok" else "not_gradeable"
            note, tot, scaled = NOTE.get(status, ""), "", ""
        rows.append({"Student Code": st.student_code, "Email": st.email,
                     "Type": st.submission_type, "Status": status,
                     "Total_100": tot, "Scaled_10": scaled, "Note": note})
    return rows
