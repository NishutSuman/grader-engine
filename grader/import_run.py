"""Import a completed file-based run (`runs/<slug>/`) into the DB as a finalized
eval — so the gradings we did before GradePilot show up on the platform.

Reads config.yaml (assignment + parts + branding), rubric.md / problem_statement.md,
and grading_master.csv (one row per student, columns `<PartKey>_score/_max/_feedback`,
`Total_Score`, optional `Overall`/`Name`/`Email`). Report cards are matched by
`report_cards/<User Code>.pdf`. Grades carry no evidence (pre-evidence runs).

Idempotent: skips a slug that already exists unless overwrite=True.
"""
from __future__ import annotations

import csv
from pathlib import Path

import yaml

from app.db import repo
from app.db.models import Eval, Grade, Part, Student
from app.db.session import get_session


def _rubric_md(run_dir: Path, parts: list[dict]) -> str:
    for name in ("rubric.md", "rubric.txt"):
        p = run_dir / name
        if p.exists():
            return p.read_text()
    # reconstruct from config parts
    out = []
    for p in parts:
        out.append(f"## {p['key']} — {p.get('title', p['key'])} ({p.get('total_marks', 0)})")
        for crit, m in (p.get("breakdown") or {}).items():
            out.append(f"- {crit} — {m}")
        out.append("")
    return "\n".join(out)


def _f(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def import_run(run_dir: str | Path, status: str = "finalized",
               overwrite: bool = False) -> dict:
    run_dir = Path(run_dir).resolve()
    cfg = yaml.safe_load((run_dir / "config.yaml").read_text()) or {}
    a = cfg.get("assignment", {})
    parts = cfg.get("parts", [])
    slug = a["slug"]

    s = get_session()
    existing = repo.get_eval(s, slug)
    if existing:
        if not overwrite:
            return {"slug": slug, "skipped": True, "reason": "already imported"}
        s.delete(existing)
        s.commit()

    cards_dir = run_dir / "report_cards"
    ps = run_dir / "problem_statement.md"
    total = a.get("total_marks") or int(round(sum(p.get("total_marks", 0) for p in parts)))
    model = (cfg.get("model", {}) or {}).get("grade") or (cfg.get("model", {}) or {}).get("model") or "imported"

    ev = Eval(slug=slug, title=a.get("title", slug), total_marks=total,
              normalize_to=a.get("normalize_to", 10), grader_model=str(model),
              rubric_md=_rubric_md(run_dir, parts),
              problem_statement_md=ps.read_text() if ps.exists() else "",
              status=status,
              config_json={"source": "imported_run", "run_dir": str(run_dir),
                           "cards_dir": str(cards_dir), "branding": cfg.get("branding", {})})
    s.add(ev); s.flush()
    for i, p in enumerate(parts, 1):
        s.add(Part(eval_id=ev.id, key=p["key"], title=p.get("title", p["key"]),
                   max_marks=float(p.get("total_marks", 0)), order=i,
                   breakdown_json=p.get("breakdown") or {}))

    rows = list(csv.DictReader(open(run_dir / "grading_master.csv")))
    cols = rows[0].keys() if rows else []
    name_col = next((c for c in ("User Name", "Name") if c in cols), None)
    email_col = "Email" if "Email" in cols else None

    n_students = n_cards = n_grades = 0
    for r in rows:
        code = (r.get("User Code") or "").strip()
        if not code:
            continue
        card = cards_dir / f"{code}.pdf"
        has_card = card.exists()
        st = Student(eval_id=ev.id, student_code=code,
                     name=(r.get(name_col) or "").strip() if name_col else "",
                     email=(r.get(email_col) or "").strip() if email_col else "",
                     submission_type="imported", download_status="ok",
                     grade_status="graded", total_score=_f(r.get("Total_Score")),
                     overall_feedback=(r.get("Overall") or "").strip(),
                     download_meta_json={"report": {"card_file": str(card) if has_card else None,
                                                    "has_card": has_card}})
        s.add(st); s.flush()
        for p in parts:
            k = p["key"]
            if f"{k}_score" not in r:
                continue
            s.add(Grade(student_id=st.id, part_key=k, score=_f(r.get(f"{k}_score")),
                        max=_f(r.get(f"{k}_max")) or float(p.get("total_marks", 0)),
                        feedback=(r.get(f"{k}_feedback") or "").strip(),
                        evidence_json=[], model=str(model)))
            n_grades += 1
        n_students += 1
        n_cards += 1 if has_card else 0

    repo.audit(s, "import_run", eval_id=ev.id,
               after={"students": n_students, "grades": n_grades, "cards": n_cards})
    s.commit()
    return {"slug": slug, "students": n_students, "grades": n_grades,
            "cards": n_cards, "parts": len(parts), "total_marks": total}
