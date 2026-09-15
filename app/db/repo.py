"""Repository helpers — the only place that queries the DB, so engine/web/ticket
code stays persistence-agnostic. All functions take an open Session."""
from __future__ import annotations

from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import AuditLog, Eval, Grade, Part, Student, Ticket


# ── Evals ──────────────────────────────────────────────────────────────────
def get_eval(s: Session, slug: str) -> Optional[Eval]:
    return s.scalar(select(Eval).where(Eval.slug == slug))


def get_eval_by_id(s: Session, eval_id: int) -> Optional[Eval]:
    return s.get(Eval, eval_id)


def list_evals(s: Session) -> list[Eval]:
    return list(s.scalars(select(Eval).order_by(Eval.created_at.desc())))


def parts(s: Session, eval_id: int) -> list[Part]:
    return list(s.scalars(select(Part).where(Part.eval_id == eval_id).order_by(Part.order)))


# ── Students ───────────────────────────────────────────────────────────────
def students(s: Session, eval_id: int) -> list[Student]:
    return list(s.scalars(select(Student).where(Student.eval_id == eval_id)
                          .order_by(Student.student_code)))


def get_student(s: Session, eval_id: int, code: str) -> Optional[Student]:
    return s.scalar(select(Student).where(Student.eval_id == eval_id,
                                          Student.student_code == code))


def students_needing_download(s: Session, eval_id: int, force: bool = False) -> list[Student]:
    q = select(Student).where(Student.eval_id == eval_id,
                              Student.submission_type != "empty")
    if not force:
        q = q.where(Student.download_status != "ok")
    return list(s.scalars(q.order_by(Student.student_code)))


# Statuses that carry gradeable content on disk. 'partial' = the repo/folder downloaded
# but some files inside failed — we grade the student on what DID come through rather than
# silently skipping them (a partial repo is still a real submission; dropping it would give
# no score at all, which is worse than grading the available work).
GRADEABLE_DL_STATUSES = ("ok", "partial")


def students_to_grade(s: Session, eval_id: int) -> list[Student]:
    return list(s.scalars(
        select(Student).where(Student.eval_id == eval_id,
                              Student.download_status.in_(GRADEABLE_DL_STATUSES))
        .order_by(Student.student_code)))


# ── Grades ─────────────────────────────────────────────────────────────────
def upsert_grade(s: Session, student_id: int, part_key: str, **fields) -> Grade:
    g = s.scalar(select(Grade).where(Grade.student_id == student_id,
                                     Grade.part_key == part_key))
    if g is None:
        g = Grade(student_id=student_id, part_key=part_key)
        s.add(g)
    for k, v in fields.items():
        setattr(g, k, v)
    return g


def grades_for(s: Session, student_id: int) -> list[Grade]:
    return list(s.scalars(select(Grade).where(Grade.student_id == student_id)))


def all_grades(s: Session, eval_id: int) -> list[Grade]:
    """Every Grade for an eval in one query (for the results table / CSV)."""
    return list(s.scalars(select(Grade).join(Student, Grade.student_id == Student.id)
                          .where(Student.eval_id == eval_id)))


# ── Audit ──────────────────────────────────────────────────────────────────
def audit(s: Session, action: str, *, eval_id=None, student_id=None,
          actor="system", before=None, after=None) -> AuditLog:
    a = AuditLog(eval_id=eval_id, student_id=student_id, actor=actor,
                 action=action, before_json=before or {}, after_json=after or {})
    s.add(a)
    return a


# ── Tickets ────────────────────────────────────────────────────────────────
def get_ticket(s: Session, lms_ticket_id: str) -> Optional[Ticket]:
    return s.scalar(select(Ticket).where(Ticket.lms_ticket_id == lms_ticket_id))
