"""No-decrease reconciliation — promoted from the per-run reconcile.py so both
finalize and the ticket loop share one policy: a regrade never lowers a
published score. Every decision is written to the audit log."""
from __future__ import annotations

from typing import Optional

from app.db import repo
from sqlalchemy.orm import Session


def reconcile(original: Optional[float], new: float) -> tuple[float, bool, str]:
    """Return (published, changed, reason) under the no-decrease policy."""
    if original is None:
        return new, True, "newly graded (was uncaptured)"
    if new > original:
        return new, True, "raised (corrected under-grade)"
    return original, False, "kept original (no decrease)"


def apply_regrade(s: Session, student, original_total: Optional[float], new_total: float,
                  actor: str = "system") -> dict:
    """Reconcile a regrade for one student, persist the published total + audit."""
    published, changed, reason = reconcile(original_total, new_total)
    before = {"total": student.total_score}
    student.total_score = published
    repo.audit(s, "regrade", eval_id=student.eval_id, student_id=student.id, actor=actor,
               before=before, after={"original": original_total, "new": new_total,
                                     "published": published, "changed": changed, "reason": reason})
    return {"original": original_total, "new": new_total, "published": published,
            "changed": changed, "reason": reason}
