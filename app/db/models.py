"""GradePilot SQLite schema (SQLAlchemy 2.0).

The database is the source of truth for structured state — evals, students,
per-section grades WITH evidence, tickets, jobs, and an immutable audit trail.
Binary artifacts (downloaded PDFs, rendered cards) live on disk under
``data/<eval-slug>/`` and are referenced by path, not stored as blobs.

Each table maps to a state file the per-run scripts used to write:
  Eval     <- config.yaml            Student <- submissions_raw.csv + download_log.json
  Part     <- config parts:          Grade   <- regrade_results/*.json
  Ticket   <- regrading_requests.csv + delta_report.csv
  Job      <- batch_state.json       AuditLog <- (new) every score mutation
"""
from __future__ import annotations

import datetime as _dt
from typing import Any, Optional

from sqlalchemy import (
    JSON, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def _now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


class Eval(Base):
    """One cohort/assignment run — the unit you context-switch between."""
    __tablename__ = "evals"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String, unique=True, index=True)
    title: Mapped[str] = mapped_column(String)
    total_marks: Mapped[int] = mapped_column(Integer, default=100)
    normalize_to: Mapped[int] = mapped_column(Integer, default=10)
    grader_model: Mapped[str] = mapped_column(String, default="claude-sonnet-4-6")
    # Full intake context kept verbatim so grading is reproducible + auditable.
    config_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    rubric_md: Mapped[str] = mapped_column(Text, default="")
    problem_statement_md: Mapped[str] = mapped_column(Text, default="")
    # setup | downloading | grading | graded | finalized
    status: Mapped[str] = mapped_column(String, default="setup")
    created_at: Mapped[_dt.datetime] = mapped_column(DateTime, default=_now)

    parts: Mapped[list["Part"]] = relationship(back_populates="eval", cascade="all, delete-orphan")
    students: Mapped[list["Student"]] = relationship(back_populates="eval", cascade="all, delete-orphan")


class Part(Base):
    """A rubric section (S1..S7). breakdown_json = {criterion: marks}."""
    __tablename__ = "parts"
    __table_args__ = (UniqueConstraint("eval_id", "key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    eval_id: Mapped[int] = mapped_column(ForeignKey("evals.id"), index=True)
    key: Mapped[str] = mapped_column(String)               # e.g. S1_Audit_Friction
    title: Mapped[str] = mapped_column(String)             # e.g. "S1 — Product Audit..."
    max_marks: Mapped[float] = mapped_column(Float)
    order: Mapped[int] = mapped_column(Integer, default=0)
    breakdown_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    problem_statement: Mapped[str] = mapped_column(Text, default="")

    eval: Mapped["Eval"] = relationship(back_populates="parts")


class Student(Base):
    """One student in a cohort + their submission + download outcome."""
    __tablename__ = "students"
    __table_args__ = (UniqueConstraint("eval_id", "student_code"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    eval_id: Mapped[int] = mapped_column(ForeignKey("evals.id"), index=True)
    student_code: Mapped[str] = mapped_column(String, index=True)
    name: Mapped[str] = mapped_column(String, default="")
    email: Mapped[str] = mapped_column(String, default="")
    submission_raw: Mapped[str] = mapped_column(Text, default="")
    # drive_folder | drive_file | gdoc | github | s3 | url | inline | onedrive | empty
    submission_type: Mapped[str] = mapped_column(String, default="empty")
    # pending | ok | empty | restricted | not_gradeable | failed | not_submitted
    download_status: Mapped[str] = mapped_column(String, default="pending")
    download_meta_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # graded | not_gradeable | not_submitted | pending
    grade_status: Mapped[str] = mapped_column(String, default="pending")
    total_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    overall_feedback: Mapped[str] = mapped_column(Text, default="")

    eval: Mapped["Eval"] = relationship(back_populates="students")
    grades: Mapped[list["Grade"]] = relationship(back_populates="student", cascade="all, delete-orphan")


class Grade(Base):
    """Per-section score with the EVIDENCE behind it — the anti-hallucination
    substrate and the basis for grounded ticket rebuttals."""
    __tablename__ = "grades"
    __table_args__ = (UniqueConstraint("student_id", "part_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("students.id"), index=True)
    part_key: Mapped[str] = mapped_column(String)
    score: Mapped[float] = mapped_column(Float, default=0)
    max: Mapped[float] = mapped_column(Float, default=0)
    feedback: Mapped[str] = mapped_column(Text, default="")
    # [{quote, source_file, page}] — cited evidence for every deduction/award
    evidence_json: Mapped[list[Any]] = mapped_column(JSON, default=list)
    # The RAW master-grader findings for this section, BEFORE the learner-facing
    # rewrite ({score, max, feedback, deductions, improvement, evidence} - the same
    # shape `render_feedback`/`feedback_style.rewrite_student_feedback` consume).
    # Durable on purpose: `feedback` is derived text that can be regenerated any
    # time the rewrite prompt/logic changes (bug fix, tone change, etc.) WITHOUT
    # re-calling the expensive master grading model - only this was ever missing
    # historically, which is exactly what made the 2026-07-13 truncation bug and
    # every prompt revision since unrecoverable without a full regrade.
    raw_findings_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    model: Mapped[str] = mapped_column(String, default="")
    batch_id: Mapped[str] = mapped_column(String, default="")
    graded_at: Mapped[_dt.datetime] = mapped_column(DateTime, default=_now, onupdate=_now)

    student: Mapped["Student"] = relationship(back_populates="grades")


class Ticket(Base):
    """An LMS score challenge — fetched via Playwright, resolved with no-decrease."""
    __tablename__ = "tickets"

    id: Mapped[int] = mapped_column(primary_key=True)
    eval_id: Mapped[Optional[int]] = mapped_column(ForeignKey("evals.id"), nullable=True, index=True)
    student_id: Mapped[Optional[int]] = mapped_column(ForeignKey("students.id"), nullable=True)
    lms_ticket_id: Mapped[str] = mapped_column(String, index=True)
    concern_text: Mapped[str] = mapped_column(Text, default="")
    decision: Mapped[str] = mapped_column(String, default="")   # regraded | explained | escalated
    original_total: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    new_total: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    published_total: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    reason: Mapped[str] = mapped_column(Text, default="")
    draft_response: Mapped[str] = mapped_column(Text, default="")
    # fetched | drafted | qc_approved | posted | closed | error
    status: Mapped[str] = mapped_column(String, default="fetched")
    created_at: Mapped[_dt.datetime] = mapped_column(DateTime, default=_now)


class Job(Base):
    """A background pipeline task (download|grade|report|ticket) + live progress."""
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    eval_id: Mapped[Optional[int]] = mapped_column(ForeignKey("evals.id"), nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="queued")  # queued|running|done|error
    progress: Mapped[str] = mapped_column(String, default="")
    log: Mapped[str] = mapped_column(Text, default="")
    batch_id: Mapped[str] = mapped_column(String, default="")
    created_at: Mapped[_dt.datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[_dt.datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class AuditLog(Base):
    """Immutable record of every score mutation — QC edits, regrades, no-decrease keeps."""
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    eval_id: Mapped[Optional[int]] = mapped_column(ForeignKey("evals.id"), nullable=True, index=True)
    student_id: Mapped[Optional[int]] = mapped_column(ForeignKey("students.id"), nullable=True)
    actor: Mapped[str] = mapped_column(String, default="system")   # system | user
    action: Mapped[str] = mapped_column(String)
    before_json: Mapped[Any] = mapped_column(JSON, default=dict)
    after_json: Mapped[Any] = mapped_column(JSON, default=dict)
    at: Mapped[_dt.datetime] = mapped_column(DateTime, default=_now)
