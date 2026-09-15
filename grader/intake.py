"""Intake — turn (submission sheet, rubric.md, problem_statement.md) into an
Eval + Parts + Students in the DB. Replaces hand-authored config.yaml.

Rubric format understood (the shape already used across runs):
    ## S1 — Product Audit & Friction Analysis (18)
    - Product/flow choice well-justified — 2
    - User-flow map complete & clear — 3
Header `(N)` is the section max; if absent it's the sum of bullet marks.
Bullet marks may be trailing ` — N`, ` : N`, or ` (N)`.
"""
from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Optional

from app.db import repo
from app.db.models import Eval, Part, Student
from app.db.session import get_session
from grader.download import classify, _looks_like_code

_HEADER = re.compile(r"^#{2,3}\s+(.+?)\s*(?:\((\d+(?:\.\d+)?)\))?\s*$")
_BULLET = re.compile(r"^\s*[-*]\s+(.*?)\s*(?:[—:\-]\s*|\()(\d+(?:\.\d+)?)\)?\s*$")
_CODE = re.compile(r"^([A-Za-z]{1,4}\d{1,3}|Part\s*\d+|Section\s*\d+)\b", re.I)


def _detitle_em_dash(title: str) -> str:
    """Part.title is rendered verbatim as the section heading on every report
    card (report_pdf.py's SECTION SCORES/SECTION FEEDBACK headers) - keep it
    free of em/en dashes per the no-em-dash policy. Uses a plain hyphen, not
    grade.strip_em_dashes()'s comma (that's tuned for prose sentences; a
    heading like "Part 1 — Core App" reads naturally as "Part 1 - Core App",
    not "Part 1, Core App"). This is also the separator the key-parsing logic
    just below already treats as equivalent to '—' and ':' (see `rest = ...`)."""
    t = re.sub(r"\s+[—–]\s+", " - ", title)         # spaced dash -> " - "
    return t.replace("—", "-").replace("–", "-")     # any remainder -> hyphen


def parse_rubric(md: str) -> list[dict]:
    """→ [{key, title, max_marks, breakdown, order}]. Robust to loose formatting."""
    parts, cur = [], None
    for line in md.splitlines():
        h = _HEADER.match(line)
        b = _BULLET.match(line)
        if h and (h.group(2) or _CODE.match(h.group(1)) or "—" in h.group(1) or "-" in h.group(1)):
            if cur:
                parts.append(cur)
            title = _detitle_em_dash(h.group(1).strip())
            cur = {"title": title, "declared_max": float(h.group(2)) if h.group(2) else None,
                   "breakdown": {}}
        elif b and cur is not None:
            cur["breakdown"][b.group(1).strip()] = float(b.group(2))
    if cur:
        parts.append(cur)
    # finalize: keys, maxes, order
    out = []
    for i, p in enumerate(parts, 1):
        bd_sum = sum(p["breakdown"].values())
        mx = p["declared_max"] if p["declared_max"] is not None else bd_sum
        cm = _CODE.match(p["title"])
        code = re.sub(r"\s+", "", cm.group(1)).upper() if cm else f"P{i}"
        # append a short slug so keys are readable + unique. Strip exactly what _CODE
        # matched (using cm.end(), not a second regex) — a separate "^[A-Za-z0-9]+\s*[-:]"
        # pattern only strips a code with NO internal space ("part1 —"), so the SAME
        # logical title produced a different key depending on whether it was written
        # "part1 —" (Metabase auto-intake's header) or "Part 1 —" (a hand-edited rubric
        # re-save) — silently desyncing Part.key from a question_map computed earlier.
        rest = re.sub(r"^\s*[—:\-]\s*", "", p["title"][cm.end():]) if cm else p["title"]
        slug = "_".join(re.findall(r"[A-Za-z]+", rest)[:3])
        key = f"{code}_{slug}" if slug else code
        out.append({"key": key, "title": p["title"], "max_marks": mx,
                    "breakdown": p["breakdown"], "order": i})
    return out


def _detect(cols: list[str], *needles: str) -> Optional[str]:
    for n in needles:
        for c in cols:
            if n in c.lower():
                return c
    return None


# ── value-aware column detection ─────────────────────────────────────────────
# Header names alone are unreliable — OPs mislabel/misorder columns (a 'Username'
# header can hold student IDs, dropping the ID into the name field). So we score
# each column on BOTH its header AND what its cells actually look like.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-./]{2,39}$")
_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z .,'\-]{1,59}$")
_HEADER_HINTS = {
    "email": ("email", "e-mail", "mail id", "mail"),
    "submission": ("submission link", "submission", "answer raw", "github", "repo",
                   "drive", "solution", "answer", "link", "url"),
    "id": ("student code", "user code", "student id", "user id", "roll", "enrol",
           "reg no", "code", "user", "id"),
    "name": ("student name", "full name", "candidate name", "name"),
}


def _has_link(v: str) -> bool:
    lv = v.lower()
    return any(k in lv for k in ("http", "href=", "drive.google", "github.com", "<a "))


def _is_id(v: str) -> bool:
    return (" " not in v and bool(_ID_RE.match(v)) and any(ch.isdigit() for ch in v)
            and not _EMAIL_RE.match(v) and not _has_link(v))


def _is_name(v: str) -> bool:
    return (bool(_NAME_RE.match(v)) and not any(ch.isdigit() for ch in v)
            and 1 <= len(v.split()) <= 5 and "@" not in v)


def _signals(vals: list[str]) -> dict:
    n = len(vals) or 1
    return {"email": sum(_EMAIL_RE.match(v) is not None for v in vals) / n,
            "link": sum(_has_link(v) for v in vals) / n,
            "id": sum(_is_id(v) for v in vals) / n,
            "name": sum(_is_name(v) for v in vals) / n}


def _header_hit(col: str, role: str) -> float:
    low = col.lower()
    for i, h in enumerate(_HEADER_HINTS[role]):
        if h in low:
            return 1.0 - i * 0.03
    return 0.0


def detect_columns(rows: list[dict], col_map: Optional[dict] = None,
                   exclude: Optional[list] = None) -> dict:
    """Map id / name / email / submission columns from BOTH header hints and cell
    VALUES, so a mislabelled or misordered sheet still resolves correctly. Any
    role in `col_map` is honoured verbatim (manual override); `exclude` columns
    are never assigned (e.g. the question-ID column in a long sheet)."""
    cm = col_map or {}
    cols = list(rows[0].keys()) if rows else []
    vals = {c: [(r.get(c) or "").strip() for r in rows if (r.get(c) or "").strip()] for c in cols}
    sig = {c: _signals(vals[c]) for c in cols}
    assigned = {}
    used = {v for v in cm.values() if v} | set(exclude or [])
    # Lock the value-distinctive roles first (email, submission), then id, then name.
    for role, vkey, need in (("email", "email", 0.5), ("submission", "link", 0.3),
                             ("id", "id", 0.15), ("name", "name", 0.15)):
        if cm.get(role):
            assigned[role] = cm[role]; continue
        best, best_s = None, 0.0
        for c in cols:
            if c in used:
                continue
            s = sig[c][vkey] + 0.6 * _header_hit(c, role)
            if s > best_s:
                best, best_s = c, s
        if best is not None and best_s >= need:
            assigned[role] = best; used.add(best)
        else:
            assigned[role] = None
    if not assigned.get("id"):                          # id is required → last-resort fallbacks
        free = [c for c in cols if c not in (exclude or [])]
        assigned["id"] = assigned.get("email") or (free[0] if free else None)
    return assigned


def links_from_raw(raw: str) -> list[dict]:
    """A cell → cleaned submission links [{raw, type, value}]. Strips HTML, pulls
    every URL, dedups. Non-URL content (pasted text) is kept as one inline entry;
    blank/'not attempted' → []."""
    from grader.download import extract_links, link_is_whole_answer
    s = (raw or "").strip()
    if not s:
        return []
    urls = extract_links(raw)
    if urls:
        # Only keep the WHOLE cell as inline text (over just the URL(s)) when there's
        # real code sitting alongside the link — a pasted answer can legitimately
        # CITE a URL inside actual code (e.g. this eval's Part 4 requires calling an
        # LLM API endpoint like OpenRouter from code) without that URL being the
        # submission. But a cover-letter-style cell ("GitHub Repository Link: <url>.
        # Note: this repo contains...") is NOT a case of "real content beside an
        # incidental link" — the prose there is a caption describing where the real
        # work lives, not a second deliverable, so the link is still the actual
        # submission regardless of how long that caption runs. Checking residual
        # length alone conflated these two cases; requiring actual code syntax tells
        # them apart.
        residual = s
        for u in urls:
            residual = residual.replace(u, " ")
        whole = all(link_is_whole_answer(s, u) for u in urls)
        if whole or not _looks_like_code(residual):
            if len(urls) == 1:
                # Single link, essentially the whole cell (whole=True) OR a longer
                # caption that code-detection still says isn't a second real
                # deliverable (whole=False, via the _looks_like_code check above).
                # These two cases need DIFFERENT classify() inputs, not the same
                # one - confirmed live this was silently undoing the code-detection
                # fix entirely (15 real students, same eval): calling classify(s)
                # unconditionally re-runs classify()'s OWN internal, cruder,
                # length-only link_is_whole_answer() check on the full cell text -
                # so a longer caption ("GitHub Repository: <url>. Module Location:
                # ... includes: - Scraping scripts - SQLite DB ...", 750-960 chars,
                # comfortably over that check's 300-char default) got silently
                # reclassified right back to "inline" regardless of what this
                # function had just decided. Only use the full-cell text (classify(s))
                # when whole=True — that's specifically for extract_links()'s URL
                # pattern truncating a link with a literal unencoded space (e.g. an
                # S3 file-upload key built from the student's original filename,
                # ".../SAHIBA ASSIGNMENT (2).pdf"); classify() has dedicated logic
                # to keep the whole cell intact for exactly that, but only when
                # given the untruncated text. When whole=False, classify the
                # cleanly-extracted URL directly instead, so the length check that
                # got us here isn't immediately re-applied and reversed.
                stype, val = classify(s) if whole else classify(urls[0])
                return [] if stype == "empty" else [{"raw": s, "type": stype, "value": val}]
            return [{"raw": u, **dict(zip(("type", "value"), classify(u)))} for u in urls]
        stype, val = classify(s)
        return [] if stype == "empty" else [{"raw": s, "type": stype, "value": val}]
    stype, val = classify(raw or "")
    return [] if stype == "empty" else [{"raw": (raw or "").strip(), "type": stype, "value": val}]


def _agg_type(links: list[dict]) -> str:
    if not links:
        return "empty"
    types = {l["type"] for l in links}
    return next(iter(types)) if len(types) == 1 else "mixed"


def students_from_rows(rows: list[dict], col_map: Optional[dict] = None) -> list[dict]:
    """Detect id/email/name/submission columns (value-aware) → students, with
    HTML-cleaned, de-duplicated, multi-link submissions. Shared by the CSV intake
    and the submissions-tab loader."""
    if not rows:
        return []
    cols = detect_columns(rows, col_map)
    id_col, sub_col = cols["id"], cols["submission"]
    name_col, email_col = cols["name"], cols["email"]
    if not id_col:
        raise ValueError("no student id/email/roll column found — add a header like "
                         "'Roll No', 'Email', or 'Student Code'")
    out = []
    for r in rows:
        code = (r.get(id_col) or "").strip()
        if not code:
            continue
        raw = (r.get(sub_col) or "").strip() if sub_col else ""
        links = links_from_raw(raw)
        submission_raw = "\n".join(dict.fromkeys(l["raw"] for l in links))
        out.append({"student_code": code,
                    "name": (r.get(name_col) or "").strip() if name_col else "",
                    "email": (r.get(email_col) or "").strip() if email_col else "",
                    "submission_raw": submission_raw, "submission_type": _agg_type(links),
                    "links": links})
    return out


def parse_sheet(path: Path, col_map: Optional[dict] = None) -> list[dict]:
    return students_from_rows(list(csv.DictReader(open(path))), col_map)


def ingest(slug: str, title: str, sheet_path: str, rubric_path: str,
           ps_path: Optional[str] = None, total_marks: Optional[int] = None,
           normalize_to: int = 10, grader_model: str = "claude-sonnet-5",
           col_map: Optional[dict] = None, config: Optional[dict] = None) -> int:
    """Create an eval from files. Returns eval_id. Fails if slug exists."""
    s = get_session()
    if repo.get_eval(s, slug):
        raise ValueError(f"eval '{slug}' already exists")
    rubric_md = Path(rubric_path).read_text()
    ps_md = Path(ps_path).read_text() if ps_path and Path(ps_path).exists() else ""
    part_defs = parse_rubric(rubric_md)
    if not part_defs:
        raise ValueError("no rubric sections parsed — check rubric.md headers")
    tmax = total_marks or int(round(sum(p["max_marks"] for p in part_defs)))

    ev = Eval(slug=slug, title=title, total_marks=tmax, normalize_to=normalize_to,
              grader_model=grader_model, rubric_md=rubric_md, problem_statement_md=ps_md,
              config_json=config or {}, status="setup")
    s.add(ev); s.flush()
    for p in part_defs:
        s.add(Part(eval_id=ev.id, key=p["key"], title=p["title"], max_marks=p["max_marks"],
                   order=p["order"], breakdown_json=p["breakdown"]))
    students = parse_sheet(Path(sheet_path), col_map)
    for st in students:
        s.add(Student(eval_id=ev.id, download_status="not_submitted" if st["submission_type"] == "empty" else "pending",
                      grade_status="not_submitted" if st["submission_type"] == "empty" else "pending", **st))
    s.commit()
    repo.audit(s, "ingest", eval_id=ev.id, after={"parts": len(part_defs), "students": len(students), "total_marks": tmax})
    s.commit()
    return ev.id
