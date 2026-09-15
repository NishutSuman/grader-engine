"""Read the 'Pending Gradings' tracker sheet and auto-pick THIS WEEK's gradings.

The Outcomes team keeps a master Google Sheet of every grading request with its
deadlines and status. This module reads that (private, Masai-owned) sheet via the
**same service account** used for Drive downloads — it is shared to the SA as
Viewer, so no interactive OAuth is needed — and auto-picks the gradings due this
week, so the grader can pull them in with one button.

Auto-pick rule (confirmed with the user):
  * gradable  = Status is not "Grading Completed" and not "Submission(s) Not
                Received" (i.e. submissions are in and it still needs grading).
  * this week = the EARLIER of {Grading Deadline (T+14), Expected Handover
                Deadline} falls on/before the end of the current week (Sunday).
  * overdue   = that effective deadline is already in the past — INCLUDED and
                flagged, sorted to the top.

Config (env, with sane defaults):
  GRADEPILOT_SHEET_ID   spreadsheet id  (default: the Outcomes tracker)
  GRADEPILOT_SHEET_TAB  tab/worksheet   (default: "Pending Gradings")
The service-account key is auto-discovered by grader.download._sa_key_path.
"""
from __future__ import annotations

import datetime as _dt
import os
import re
import urllib.parse
from typing import Optional

import requests

from app.db import repo
from app.db.models import Eval
from app.db.session import get_session
from grader.download import _sa_key_path

SHEET_ID = os.environ.get("GRADEPILOT_SHEET_ID", "1Gg66kLtefuQfw_Kvk733SYUTqjoCk5LnunsnSOLCawQ")
SHEET_TAB = os.environ.get("GRADEPILOT_SHEET_TAB", "Pending Gradings")
_SHEETS_SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]


# ── sheet read ───────────────────────────────────────────────────────────────
def _sheets_token() -> str:
    """Bearer token for the Sheets API (own creds — Drive scope differs)."""
    from google.auth.transport.requests import Request as GRequest
    from google.oauth2 import service_account
    creds = service_account.Credentials.from_service_account_file(
        str(_sa_key_path({})), scopes=_SHEETS_SCOPES)
    creds.refresh(GRequest())
    return creds.token


_ROWS_CACHE: dict[tuple[str, str], tuple[float, list[dict]]] = {}
_ROWS_CACHE_TTL = 30.0   # seconds — the "This Week" page reads this on EVERY nav; without a
                         # cache that was a live Sheets round-trip on every click, the main
                         # cause of the "unusual wait" navigating into this page.


def read_rows(sheet_id: str = SHEET_ID, tab: str = SHEET_TAB, fresh: bool = False) -> list[dict]:
    """Return the tab as a list of {header: value} dicts (row order preserved).
    Cached for _ROWS_CACHE_TTL seconds (per sheet_id+tab) so repeated page loads don't
    each pay a live Sheets API round-trip. Pass fresh=True (the Fetch button does) to
    force a live read — e.g. right after OPs edits the sheet."""
    import time as _time
    key = (sheet_id, tab)
    if not fresh:
        hit = _ROWS_CACHE.get(key)
        if hit and _time.monotonic() - hit[0] < _ROWS_CACHE_TTL:
            return hit[1]
    rng = urllib.parse.quote(f"'{tab}'")
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}/values/{rng}"
    r = requests.get(url, headers={"Authorization": f"Bearer {_sheets_token()}"},
                     timeout=(10, 60))
    if r.status_code != 200:
        raise RuntimeError(f"Sheets API {r.status_code}: {r.text[:200]}")
    values = r.json().get("values", [])
    if not values:
        rows = []
    else:
        header = [h.strip() for h in values[0]]
        rows = []
        for raw in values[1:]:
            raw = raw + [""] * (len(header) - len(raw))      # pad short rows
            rows.append({header[i]: (raw[i] or "").strip() for i in range(len(header))})
    _ROWS_CACHE[key] = (_time.monotonic(), rows)
    return rows


# ── parsing helpers ──────────────────────────────────────────────────────────
_DATE = re.compile(r"(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})")


def parse_date(s: str) -> Optional[_dt.date]:
    """Day-first date parse. Handles 04/07/2026, 3/7/26, 25/6/2026."""
    m = _DATE.search(s or "")
    if not m:
        return None
    d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if y < 100:
        y += 2000
    try:
        return _dt.date(y, mo, d)
    except ValueError:
        return None


def _find(headers: list[str], *needles: str) -> Optional[str]:
    """First header containing ALL of any needle group (space-separated words)."""
    low = {h: h.lower() for h in headers}
    for group in needles:
        words = group.lower().split()
        for h in headers:
            if all(w in low[h] for w in words):
                return h
    return None


def _from_iso(s: str) -> Optional[_dt.date]:
    """Parse an ISO (YYYY-MM-DD) date we stored earlier. NOT day-first."""
    try:
        return _dt.date.fromisoformat(s) if s else None
    except ValueError:
        return None


def _week_end(today: _dt.date) -> _dt.date:
    """Sunday of the current week (Mon=0 .. Sun=6)."""
    return today + _dt.timedelta(days=6 - today.weekday())


def slugify(*parts: str) -> str:
    base = "-".join(p for p in parts if p)
    return re.sub(r"[^a-z0-9]+", "-", base.lower()).strip("-")[:60] or "eval"


def _completed(status: str) -> bool:
    return "grading completed" in status.lower()


# ── the pick ─────────────────────────────────────────────────────────────────
def normalize(rows: list[dict]) -> list[dict]:
    """Map raw sheet rows → normalized grading records with parsed deadlines."""
    if not rows:
        return []
    hdr = list(rows[0].keys())
    C = {
        "batch": _find(hdr, "batch"),
        "kind": _find(hdr, "capstone project /", "graded assignment"),
        "name": _find(hdr, "evaluation name"),
        "eval_id": _find(hdr, "evaluation id"),
        "weightage": _find(hdr, "weightage"),
        "sub_deadline": _find(hdr, "submission deadline"),
        "grading_deadline": _find(hdr, "grading deadline"),
        "link": _find(hdr, "submission link"),
        "status": _find(hdr, "status"),
        "handover": _find(hdr, "handover"),
    }

    def g(row, key):
        col = C.get(key)
        return row.get(col, "") if col else ""

    out = []
    for row in rows:
        batch = g(row, "batch")
        name = g(row, "name")
        if not batch and not name:
            continue                                          # skip blank rows
        gd = parse_date(g(row, "grading_deadline"))
        hd = parse_date(g(row, "handover"))
        eff = min([d for d in (gd, hd) if d], default=None)
        out.append({
            "batch": batch,
            "kind": g(row, "kind"),
            "name": name,
            "eval_id": g(row, "eval_id"),
            "weightage": g(row, "weightage"),
            "submission_deadline": g(row, "sub_deadline"),
            "grading_deadline": gd.isoformat() if gd else "",
            "handover_deadline": hd.isoformat() if hd else "",
            "effective_deadline": eff.isoformat() if eff else "",
            "submission_link": g(row, "link"),
            "status": g(row, "status"),
        })
    return out


def pick_week(rows: list[dict], today: Optional[_dt.date] = None) -> list[dict]:
    """Filter+sort normalized rows to this week's actionable gradings."""
    today = today or _dt.date.today()
    # Horizon = the LATER of this calendar week's Sunday and a rolling 7-day
    # lookahead. The rolling part guarantees a grading deadline a few days out
    # never hides just because it lands after Sunday (week-boundary blind spot).
    # Submission links are irrelevant here — an eval syncs on its deadline alone;
    # its submissions come from Metabase, not a submission-sheet link.
    end = max(_week_end(today), today + _dt.timedelta(days=7))
    picks = []
    for r in normalize(rows):
        # "Grading Completed" is the one status we DO trust and exclude on — a
        # completed eval is genuinely done and must never resurface just because
        # its (old) deadline still falls in the "overdue" range. Every OTHER
        # status is deliberately NOT filtered on (e.g. "Submissions Not
        # Received") — confirmed stale in practice (two evals marked "Not
        # Received" that actually had 248 and 87 real Metabase attempts), so
        # trusting those hid real, gradable work. Without any status filter at
        # all, every row with a past deadline in the sheet's entire history
        # re-surfaced, including evals closed out months ago — this exclusion
        # is what keeps that from happening while still not trusting the
        # unreliable statuses.
        if _completed(r["status"]):
            continue
        eff = _from_iso(r["effective_deadline"])
        if eff is None or eff > end:                          # undated or beyond horizon
            continue
        r = dict(r)
        r["overdue"] = eff < today
        r["days_left"] = (eff - today).days
        r["needs_triage"] = not r["status"].strip()
        r["slug"] = slugify(r["batch"], r["eval_id"] or r["name"])
        picks.append(r)
    picks.sort(key=lambda r: (_from_iso(r["effective_deadline"]) or end))
    return picks


def this_week(today: Optional[_dt.date] = None) -> list[dict]:
    """Live read + pick, in one call."""
    return pick_week(read_rows(), today)


# ── import into the grader (draft eval per row) ──────────────────────────────
def import_picks(picks: list[dict], progress=None) -> dict:
    """Upsert a DRAFT eval per pick. Idempotent by slug. Returns a summary."""
    log = progress or (lambda m: None)
    s = get_session()
    created, updated = [], []
    total = len(picks) or 1
    for i, p in enumerate(picks):
        log(f"{int(20 + i / total * 70)}|importing {p['batch']} — {p['name'] or p['slug']}…")
        title = f"{p['batch']} — {p['name']}" if p["name"] else p["batch"]
        ev = repo.get_eval(s, p["slug"])
        if ev is None:
            ev = Eval(slug=p["slug"], title=title, status="draft",
                      config_json={"pending": p, "source": "pending_sheet"})
            s.add(ev)
            created.append(p["slug"])
        else:
            cfg = dict(ev.config_json or {})
            cfg["pending"] = p
            cfg.setdefault("source", "pending_sheet")
            ev.config_json = cfg
            if ev.status == "draft":                          # refresh title only while still a draft
                ev.title = title
            updated.append(p["slug"])
    log("95|saving…")
    s.commit()
    log(f"100|done · {len(created)} new, {len(updated)} refreshed")
    return {"created": created, "updated": updated,
            "n_created": len(created), "n_updated": len(updated)}
