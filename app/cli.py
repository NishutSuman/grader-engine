"""GradePilot CLI — run a full eval end-to-end against the SQLite store.

  python -m app.cli ingest  --slug iitr --title "IITR-PM 2510" \
        --sheet runs/iitr-pm-2510/submissions_raw.csv \
        --rubric runs/iitr-pm-2510/rubric.md --ps runs/iitr-pm-2510/problem_statement.md
  python -m app.cli download --slug iitr
  python -m app.cli grade    --slug iitr
  python -m app.cli report   --slug iitr
  python -m app.cli status   --slug iitr
  python -m app.cli list
"""
from __future__ import annotations

import argparse
import collections
import sys

from app.db import repo
from app.db.session import get_session, init_db


def _eval_id(slug: str) -> int:
    ev = repo.get_eval(get_session(), slug)
    if not ev:
        sys.exit(f"no eval '{slug}' — run `ingest` first")
    return ev.id


def main():
    ap = argparse.ArgumentParser(prog="gradepilot")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("ingest"); p.add_argument("--slug", required=True); p.add_argument("--title", required=True)
    p.add_argument("--sheet", required=True); p.add_argument("--rubric", required=True)
    p.add_argument("--ps"); p.add_argument("--total", type=int); p.add_argument("--normalize", type=int, default=10)
    p.add_argument("--model", default="claude-sonnet-5")

    for name in ("download", "grade", "report", "status"):
        q = sub.add_parser(name); q.add_argument("--slug", required=True)
        if name == "download":
            q.add_argument("--workers", type=int, default=8); q.add_argument("--force", action="store_true")
        if name == "grade":
            q.add_argument("--max-tokens", type=int, default=16000)
    sub.add_parser("list")

    a = ap.parse_args()
    init_db()

    if a.cmd == "ingest":
        from grader.intake import ingest
        eid = ingest(a.slug, a.title, a.sheet, a.rubric, a.ps, a.total, a.normalize, a.model)
        s = get_session(); ev = repo.get_eval_by_id(s, eid)
        print(f"ingested eval '{a.slug}' (id={eid}): {len(repo.parts(s,eid))} parts, "
              f"{len(repo.students(s,eid))} students, total={ev.total_marks}")

    elif a.cmd == "download":
        from grader.download import download_eval
        tally = download_eval(_eval_id(a.slug), workers=a.workers, force=a.force, progress=print)
        print("download tally:", dict(tally))

    elif a.cmd == "grade":
        from grader.grade import grade_eval
        print("result:", grade_eval(_eval_id(a.slug), progress=print, max_tokens=a.max_tokens))

    elif a.cmd == "report":
        from grader.report import report_eval
        print("result:", report_eval(_eval_id(a.slug), progress=print))

    elif a.cmd == "status":
        s = get_session(); eid = _eval_id(a.slug); ev = repo.get_eval_by_id(s, eid)
        studs = repo.students(s, eid)
        print(f"eval '{ev.slug}'  status={ev.status}  total={ev.total_marks}  students={len(studs)}")
        print("  submission types:", dict(collections.Counter(st.submission_type for st in studs)))
        print("  download status :", dict(collections.Counter(st.download_status for st in studs)))
        print("  grade status    :", dict(collections.Counter(st.grade_status for st in studs)))
        graded = [st.total_score for st in studs if st.grade_status == "graded" and st.total_score is not None]
        if graded:
            import statistics as st_
            print(f"  scores/{ev.total_marks:<3}     : n={len(graded)} mean={st_.mean(graded):.1f} "
                  f"median={st_.median(graded):.1f} min={min(graded):.1f} max={max(graded):.1f}")

    elif a.cmd == "list":
        s = get_session()
        for ev in repo.list_evals(s):
            n = len(repo.students(s, ev.id))
            print(f"  {ev.slug:20} {ev.status:12} {n:4} students  {ev.title}")


if __name__ == "__main__":
    main()
