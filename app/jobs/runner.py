"""Background job runner. Pipeline steps (download/grade/report) are long-running
and synchronous, so we run them on a small thread pool, recording live progress
into the Job row. The dashboard polls the Job via HTMX.

Each job gets its own DB session for progress writes; the engine functions it
calls open their own sessions. SQLite WAL + busy_timeout absorbs the overlap."""
from __future__ import annotations

import threading
import traceback
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from app.db.models import Job
from app.db.session import get_session

_pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="gp-job")
_lock = threading.Lock()

# ── cooperative stop, per eval ───────────────────────────────────────────────
# A grade run checks stop_requested() between students; the Stop button sets it.
# Already-running students finish; every queued one is skipped.
_stops: dict[int, threading.Event] = {}


def request_stop(eval_id: int) -> None:
    _stops.setdefault(eval_id, threading.Event()).set()


def stop_requested(eval_id: int) -> bool:
    e = _stops.get(eval_id)
    return bool(e and e.is_set())


def clear_stop(eval_id: int) -> None:
    e = _stops.get(eval_id)
    if e:
        e.clear()


def clear_log(eval_id: int) -> None:
    """Reset the log window: blank the latest job's log + progress. Safe on a
    finished job (stays clear); on a running one it just re-fills as it proceeds."""
    from sqlalchemy import select
    with _lock, get_session() as ps:
        jb = ps.scalar(select(Job).where(Job.eval_id == eval_id).order_by(Job.id.desc()))
        if jb:
            jb.log = ""; jb.progress = ""
            ps.commit()


def start_job(eval_id: int | None, kind: str, fn: Callable[[Callable[[str], None]], object]) -> int:
    """Create a Job row and run fn(progress) on a worker thread. Returns job id."""
    s = get_session()
    job = Job(eval_id=eval_id, kind=kind, status="queued", progress="queued…")
    s.add(job); s.commit()
    jid = job.id
    _pool.submit(_run, jid, fn)
    return jid


def _set(jid: int, **fields):
    """Update the Job in a SHORT, immediately-committed transaction so we never
    hold SQLite's single writer lock (which would deadlock the engine's own
    writes). Retries briefly if the engine happens to be mid-commit."""
    import time as _t
    for attempt in range(6):
        try:
            with _lock, get_session() as ps:
                jb = ps.get(Job, jid)
                for k, v in fields.items():
                    setattr(jb, k, v)
                ps.commit()
            return
        except Exception:
            _t.sleep(0.5)


def _disp_log(lines: list[str], limit: int = 300) -> str:
    """Terminal view: NEWEST line first (so the latest progress stays pinned at
    the top through each poll-refresh, instead of being buried at the bottom),
    with the leading 'NN|' percent prefix stripped for readability."""
    out = []
    for l in reversed(lines[-limit:]):
        p = l.split("|", 1)
        out.append(p[1] if len(p) == 2 and p[0].isdigit() else l)
    return "\n".join(out)


def _run(jid: int, fn: Callable[[Callable[[str], None]], object]):
    _set(jid, status="running", progress="starting…")
    lines: list[str] = []

    def progress(msg: str):
        lines.append(msg)
        _set(jid, progress=msg, log=_disp_log(lines))

    try:
        result = fn(progress)
        # Headline = the last progress line (already a clean human summary), NOT str(result)
        # — dumping the raw result dict as the status made the card unreadable.
        final = lines[-1] if lines else (str(result) if result is not None else "done")
        pp = final.split("|", 1)                            # strip a leading 'NN|' percent prefix
        final = pp[1] if len(pp) == 2 and pp[0].isdigit() else final
        _set(jid, status="done", progress=final)
    except Exception:
        # error first (most relevant), then recent progress newest-first
        _set(jid, status="error", progress="error",
             log=traceback.format_exc()[-3000:] + "\n\n— recent progress —\n" + _disp_log(lines, 200))


def latest_job(eval_id: int, kind: str | None = None) -> Job | None:
    from sqlalchemy import select
    s = get_session()
    try:
        q = select(Job).where(Job.eval_id == eval_id).order_by(Job.id.desc())
        if kind:
            q = q.where(Job.kind == kind)
        return s.scalar(q)  # detached after close; scalar attrs stay readable
    finally:
        s.close()


def latest_job_by_kind(kind: str) -> Job | None:
    """Same as latest_job, but for jobs with no eval_id (e.g. the "This Week"
    tracker-sheet fetch, which isn't scoped to one eval)."""
    from sqlalchemy import select
    s = get_session()
    try:
        q = select(Job).where(Job.eval_id.is_(None), Job.kind == kind).order_by(Job.id.desc())
        return s.scalar(q)
    finally:
        s.close()


def get_job(jid: int) -> Job | None:
    s = get_session()
    try:
        return s.get(Job, jid)
    finally:
        s.close()
