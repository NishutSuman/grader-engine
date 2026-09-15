"""
Metabase connector — pull an assignment's submissions straight from the Streamline
Assess tables so we never hand-prepare a submission sheet again.

Three tables (database 34), joined per (student, question):
  - 28641 streamline_assess_question_attempts : per (student, question) — user_code,
                                 submission_id, question_id, answer (code link), score,
                                 is_submitted.
  - 28699 streamline_assess_questions          : per question — assess_question_id,
                                 question_number, question_content (problem statement +
                                 embedded ## Rubric).
  - 28640 streamline_section_user_details      : per student — email, name, batch_name
                                 (join on user_code).

Filter everything by `lms_assignment_id` (the LMS Assignment ID = our eval's LMS id,
e.g. 79650 for BITSOM_BA-2512).

Credentials come from .env: METABASE_URL, METABASE_SESSION (the metabase.SESSION
cookie; the user refreshes it when it expires).

NOTE (2026-08-10): the underlying Metabase table IDs were rotated on their side —
the previous IDs (24826/24770/24780) now return 0 rows for current cohorts (old
tables left in place but stopped receiving data). Confirmed via live query + a
schema diff (field names/order identical) that 28641/28699/28640 are the direct
replacements. If submissions ever silently stop fetching again, check this first
before assuming a code bug: query `_rows(T_ATTEMPTS, [('lms_assignment_id', <id>)])`
for a known-active eval id and see if it actually returns rows.
"""
from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path
from typing import Optional

DB_ID = 34
T_ATTEMPTS, T_QUESTIONS, T_DETAILS = 28641, 28699, 28640
_REPO = Path(__file__).resolve().parent.parent
# Same "not a real name" values grader.report_pdf._MISSING_NAME treats as missing -
# kept as its own local copy (not imported) since this fixes a different layer:
# T_DETAILS name SELECTION at fetch time, vs. report_pdf's last-resort DISPLAY
# fallback for whatever ends up stored.
_JUNK_NAMES = {"", "nan", "none", "null", "na", "n/a", "-", "--", "tbu"}


def _env() -> tuple[str, str]:
    url, sess = os.environ.get("METABASE_URL"), os.environ.get("METABASE_SESSION")
    if not (url and sess):                                  # fall back to .env (web app doesn't auto-load it)
        envp = _REPO / ".env"
        if envp.exists():
            for line in envp.read_text().splitlines():
                if "=" in line and not line.strip().startswith("#"):
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())
        url, sess = os.environ.get("METABASE_URL"), os.environ.get("METABASE_SESSION")
    if not (url and sess):
        raise RuntimeError("METABASE_URL / METABASE_SESSION missing — add them to .env")
    return url.rstrip("/"), sess


class MetabaseAuthError(RuntimeError):
    """Session token expired/invalid — the user must refresh metabase.SESSION in .env."""


def _api(method: str, path: str, body: Optional[dict] = None) -> dict:
    url, sess = _env()
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url + path, data=data, method=method,
        headers={"X-Metabase-Session": sess, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise MetabaseAuthError("Metabase session token expired — refresh METABASE_SESSION in .env")
        raise


_FCACHE: dict[int, dict[str, int]] = {}


def _fields(table_id: int) -> dict[str, int]:
    """name -> field_id (resolved live so hard-coded IDs can't rot)."""
    if table_id not in _FCACHE:
        m = _api("GET", f"/api/table/{table_id}/query_metadata")
        _FCACHE[table_id] = {f["name"]: f["id"] for f in m.get("fields", [])}
    return _FCACHE[table_id]


def _rows(table_id: int, filters=None, limit: Optional[int] = None) -> list[dict]:
    """filters: list of (field_name, value|[values]) — ANDed; a list value = IN."""
    fmap = _fields(table_id)
    query: dict = {"source-table": table_id}
    clauses = []
    for name, val in (filters or []):
        vals = list(val) if isinstance(val, (list, tuple, set)) else [val]
        clauses.append(["=", ["field", fmap[name], None], *vals])
    if clauses:
        query["filter"] = clauses[0] if len(clauses) == 1 else ["and", *clauses]
    if limit:
        query["limit"] = limit
    r = _api("POST", "/api/dataset", {"database": DB_ID, "type": "query", "query": query})
    cols = [c["name"] for c in r["data"]["cols"]]
    return [dict(zip(cols, row)) for row in r["data"]["rows"]]


def extract_rubric(content: str) -> str:
    """Pull the embedded `## Rubric` section (the marks table) out of a question's
    content. Returns '' when the question ships no rubric (caller then generates one)."""
    c = content or ""
    i = c.find("## Rubric")
    if i < 0:
        return ""
    rest = c[i:]
    nxt = rest.find("\n## ", 5)                            # stop at the next top-level heading
    return (rest[:nxt] if nxt > 0 else rest).strip()


def _clean_link(raw: str) -> str:
    from grader.download import extract_links, link_is_whole_answer, _looks_like_code
    s = (raw or "").strip()
    # A file-upload answer is a single URL whose filename may contain spaces (e.g.
    # ".../SAHIBA ASSIGNMENT.pdf"). The generic extractor stops at whitespace and
    # would chop it to ".../SAHIBA", so keep the whole URL when the cell is clearly
    # one link (single scheme, single line, no HTML). Spaces are %-encoded at fetch.
    if s.startswith("http") and s.count("http") == 1 and "\n" not in s and "<" not in s:
        return s
    links = extract_links(raw or "")
    if links:
        # link_is_whole_answer()'s length check alone missed a real pattern: a
        # cover-letter-style cell ("GitHub Repository: <url>. Module Location:
        # /data_pipeline. The complete implementation includes: - Scraping
        # scripts - SQLite DB - SQL queries...") sits ABOVE its 300-char default
        # (confirmed live: real cases ran 750-960 chars) but is still just a
        # caption describing where the actual work lives, not a second
        # deliverable - the link is still the real submission regardless of how
        # long that caption runs. Confirmed live: 15 students in one eval had a
        # genuine GitHub link silently swallowed into "inline text" this way,
        # and NONE of their repos were ever downloaded (download_status='ok' the
        # whole time - this failed completely silently). intake.py's
        # links_from_raw() already solved this exact tension with the same
        # residual-code check - mirrored here rather than reinvented, since
        # length alone can't tell a real answer from a caption but code syntax
        # in the residual can.
        residual = s
        for u in links:
            residual = residual.replace(u, " ")
        if link_is_whole_answer(s, links[0]) or not _looks_like_code(residual):
            # Prefer an actual GitHub repo link over any OTHER url type the cell
            # happens to mention first - a student's answer can easily cite a
            # localhost dev-server address or a hosted-app URL (e.g. "run it,
            # then open http://127.0.0.1:8000/") in an earlier sentence than
            # their real "GitHub Repository: <url>" line stated later. Blindly
            # taking links[0] silently graded these students against an
            # unreachable/wrong URL - confirmed live on iitp-sdai-2602-81458:
            # 4 students had a real, non-empty GitHub repo entirely dropped
            # this way. Mirrors the same fix already applied in
            # submissions_sheet.load_submissions()'s per-part candidate
            # picking - only changes behavior when a later link IS a GitHub
            # URL and an earlier one isn't; a cell with zero GitHub links, or
            # whose first link already IS the GitHub one, is unaffected.
            from grader.download import GH_RE
            github_links = [u for u in links if GH_RE.search(u)]
            return github_links[0] if github_links else links[0]
    # A cell that's HTML markup with no real text content (e.g. "<p><br></p>") is a
    # genuine non-submission, not a text answer worth keeping.
    import re
    if not re.sub(r"<[^>]+>", "", s).strip():
        return ""
    # Some parts require a typed structured-text answer directly in the LMS answer
    # box (e.g. "Submit a structured text response with Task 3.1, Task 3.2a…"),
    # not a submission link at all, and a real text answer can legitimately mention a
    # URL mid-sentence (e.g. "the student opens http://sars.university.edu") without
    # that URL being the submission. Falling back to "" here made a genuine text answer
    # indistinguishable from a non-submission — return the raw text itself so it survives.
    return s


def fetch_assignment(lms_id) -> dict:
    """All submissions for one LMS Assignment ID, joined across the three tables.
    Returns {lms_id, questions:[{question_id,number,title,problem_statement,rubric_md,
    max_marks}], students:[{user_code,uid,email,name,batch,parts:{question_id:{code,
    submitted,max_marks,lms_score}}}]}."""
    lms_id = int(lms_id)

    qrows = _rows(T_QUESTIONS, [("lms_assignment_id", lms_id)])
    questions, skipped = [], []
    for q in sorted(qrows, key=lambda x: x.get("question_number") or 0):
        content = q.get("question_content") or ""
        qtype = (q.get("question_type") or "").strip().lower()
        # Only free-response questions are gradeable here. Auto-graded objective questions
        # (MCQ single/multi-choice 'mcsc'/'mcmc', true-false, fill-ups) are scored by the
        # LMS itself — skip them so they never become a grading part. Blank/unknown types
        # are kept (older exports predate the question_type field). 'fileupload' (a direct
        # PDF/DOCX upload to S3, e.g. iimsi-dm-2511-81161's "Module 8 & 9 Project") is a
        # genuine free-response submission, not an auto-graded type — confirmed via real
        # student answer_raw links and score:0.0 (ungraded) on every row — so it belongs
        # here alongside 'subjective', not in the skip list.
        if qtype not in ("", "subjective", "fileupload"):
            skipped.append({"number": q.get("question_number"), "type": qtype,
                            "title": q.get("title")})
            continue
        pnum, ptitle = _part_header(content)                # TRUE part identity from the body header
        questions.append({
            "question_id": q.get("assess_question_id"),
            "number": q.get("question_number"),
            "question_type": qtype,
            "part": pnum,                                   # from '## Part N — …', NOT the Metabase row order
            "part_title": ptitle,
            "title": q.get("title"),
            "problem_statement": content,
            "rubric_md": extract_rubric(content),
        })
    # Order by the TRUE part number so Part 1..N come out in order regardless of how
    # Metabase numbered/ordered the questions. Unheadered questions sort last, stably.
    questions.sort(key=lambda q: q["part"] if q["part"] is not None else 10_000 + (q["number"] or 0))

    arows = _rows(T_ATTEMPTS, [("lms_assignment_id", lms_id)], limit=100000)
    codes = sorted({r.get("user_code") for r in arows if r.get("user_code")})
    details = {}
    if codes:
        # Metabase's /api/dataset endpoint hard-caps results at 2000 rows
        # SERVER-SIDE, regardless of any `limit` we request in the query body
        # (confirmed live: requesting limit=100000 still returned exactly
        # 2000). T_DETAILS is ALSO one row per (student, section/enrolment) -
        # ~10-12 rows per student, not one - so a chunk of even 300 codes can
        # produce 3000+ rows and still silently hit the cap (confirmed live: a
        # first attempt batching at 300 still lost 123 of 435 students' details
        # - blank email/name, a raw batch_id instead of batch_name). Batch small
        # enough that codes_per_batch × max_rows_per_student stays well under
        # 2000, and warn loudly (never silently) if a batch ever hits the cap
        # anyway, since that means some codes in THAT batch may be missing.
        _CHUNK = 50
        for i in range(0, len(codes), _CHUNK):
            batch = _rows(T_DETAILS, [("user_code", codes[i:i + _CHUNK])])
            if len(batch) >= 2000:
                print(f"WARNING: fetch_assignment({lms_id}) T_DETAILS batch at "
                      f"codes[{i}:{i + _CHUNK}] returned >=2000 rows - likely hit "
                      f"Metabase's row cap, some students in this batch may be "
                      f"missing details. Reduce _CHUNK further if this recurs.")
            for d in batch:
                uc = d.get("user_code")
                rec = {"email": d.get("email") or "", "name": d.get("name") or "",
                       "batch": d.get("batch_name") or ""}
                existing = details.get(uc)
                if existing is None:
                    details[uc] = rec
                elif (existing["name"].strip().lower() in _JUNK_NAMES
                        and rec["name"].strip().lower() not in _JUNK_NAMES):
                    # A student has ~10-12 T_DETAILS rows (one per section/enrolment),
                    # and the first one Metabase returns for a given user_code is not
                    # guaranteed to be the "real" enrolment - confirmed live: a stray
                    # unrelated-batch row with name="TBU" sorted before 10 correct
                    # "IITP-SDAI-2602" rows carrying the student's real name. Never
                    # DOWNGRADE an already-real name (only upgrade a junk one when a
                    # later row turns out to have a real name) - keeps this a strict
                    # improvement, never a regression, for every student regardless
                    # of row order.
                    details[uc] = rec

    students: dict[str, dict] = {}
    for a in arows:
        uc = a.get("user_code")
        if not uc:
            continue
        det = details.get(uc, {})
        st = students.setdefault(uc, {
            "user_code": uc, "uid": a.get("submission_id"), "user_id": a.get("user_id"),
            "email": det.get("email", ""), "name": det.get("name", ""),
            "batch": det.get("batch", "") or a.get("batch_id"), "parts": {}})
        st["parts"][a.get("question_id")] = {
            "code": _clean_link(a.get("answer_raw") or a.get("answer") or ""),
            "submitted": bool(a.get("is_submitted")),
            "max_marks": a.get("correct_question_score"),
            "lms_score": a.get("score"),
        }

    # Drop orphaned question rows: an LMS assignment can be deleted and recreated,
    # leaving OLD question_ids still attached to the same lms_assignment_id in
    # Metabase's table, with real students only ever answering the NEW ones. A
    # question with ZERO submitted content across the WHOLE roster, while at
    # least one other question on the same assignment has real content, is
    # almost certainly one of these orphans rather than a genuine unattempted
    # question — confirmed live on iitp-aimlt-2601-81259 (4 of 7 "questions" had
    # 0/435 submissions, differing from the real 3 only by a missing space in
    # the title, vs 360-380/435 on the real ones). Only filters on a clear
    # split — if EVERY question is at 0 (a brand-new, nobody's-submitted-yet
    # assignment), nothing gets dropped.
    submitted_counts = {q["question_id"]: 0 for q in questions}
    for st in students.values():
        for qid, p in st["parts"].items():
            if p.get("submitted") and qid in submitted_counts:
                submitted_counts[qid] += 1
    if any(submitted_counts.values()):
        dead = [q for q in questions if submitted_counts.get(q["question_id"], 0) == 0]
        if dead and len(dead) < len(questions):
            questions = [q for q in questions if q not in dead]
            skipped.extend({"number": q.get("number"), "type": "orphaned-zero-submissions",
                            "title": q.get("title"), "question_id": q.get("question_id")}
                           for q in dead)

    # LMS max per question (correct_question_score) — the number the LMS scores each
    # question out of. NOT always 1, and NOT the same across questions. Sum = LMS total.
    lms_max: dict = {}
    for a in arows:
        qid, v = a.get("question_id"), a.get("correct_question_score")
        if qid and v is not None:
            lms_max[qid] = max(lms_max.get(qid, 0.0), float(v))
    for q in questions:
        q["lms_max"] = lms_max.get(q["question_id"], 1.0)

    return {"lms_id": lms_id, "questions": questions, "students": list(students.values()),
            "skipped": skipped}


# ── build the submission sheet + eval parts from an assignment ────────────────
import re as _re

SUB_COLS = ["email", "StudentId", "uid", "name", "batch", "Question_ID", "Part", "code"]


def _rubric_total(rubric_md: str):
    m = _re.search(r"Total\**\s*\|\s*\**\s*(\d+(?:\.\d+)?)", rubric_md or "")
    return float(m.group(1)) if m else None


def _part_title(ps: str) -> str:
    for ln in (ps or "").splitlines():
        ln = ln.strip()
        if ln.startswith("# ") and not ln.startswith("## "):
            return ln[2:].strip()
    return ""


def _part_header(ps: str) -> tuple:
    """Parse the '## Part N — Title (X marks)' heading that opens a question body → (N, 'Title').
    This heading is the TRUE part identity; the Metabase question_number does NOT reliably
    reflect it (a question numbered 1 can carry 'Part 4'). Returns (None, '') when absent."""
    for ln in (ps or "").splitlines():
        m = _re.match(r"#{1,4}\s*Part\s+(\d+)\s*[—–:\-]\s*(.+)", ln.strip(), _re.I)
        if m:
            title = _re.sub(r"\s*\(\s*\d+(?:\.\d+)?\s*marks?\s*\)\s*$", "", m.group(2).strip(), flags=_re.I)
            return int(m.group(1)), title.strip()
    return None, ""


_HDR_MARKS = _re.compile(r"#{1,4}\s*Part\s+\d+\s*[—–:\-].+?\(\s*(\d+(?:\.\d+)?)\s*marks?\s*\)", _re.I)
_PS_TASK = _re.compile(r"\*\*\s*Task\s+([\d.]+)\s*[—–:\-]\s*(.+?)\s*\(\s*(\d+(?:\.\d+)?)\s*marks?\s*\)\s*\*\*", _re.I)


def _header_marks(ps: str):
    """The '(N marks)' total declared in the '## Part N — … (N marks)' heading, or None."""
    m = _HDR_MARKS.search(ps or "")
    return float(m.group(1)) if m else None


def _ps_tasks(ps: str) -> list:
    """Derive per-task rubric criteria from a problem statement's '**Task N.x — Title (M marks)**'
    lines → [('Task N.x — Title', M)]. Used when the question ships no embedded '## Rubric' table
    (marks live only in the task lines). Deterministic, no LLM, and it keeps every part scored on
    its own real weightage (e.g. Part 4 = 15, not a flat default)."""
    out = []
    for tn, tt, tm in _PS_TASK.findall(ps or ""):
        out.append((f"Task {tn} — {tt.strip()}", float(tm)))
    return out


def parse_rubric_table(rubric_md: str) -> list[tuple[str, float]]:
    """Parse the '| Evaluation Area | Marks |' table → [(criterion, marks)], drop Total."""
    out = []
    for ln in (rubric_md or "").splitlines():
        ln = ln.strip()
        if not ln.startswith("|"):
            continue
        cells = [c.strip() for c in ln.strip("|").split("|")]
        if len(cells) < 2:
            continue
        crit = cells[0].strip("* ").strip()
        mk = cells[-1].strip("* ").strip()
        low = crit.lower()
        if not crit or low in ("evaluation area", "area", "criteria", "total") or set(crit) <= set("-:"):
            continue
        try:
            out.append((crit, float(mk)))
        except ValueError:
            continue
    return out


def submission_rows(data: dict) -> list[dict]:
    """One row per (student, question) — lean columns only."""
    qorder = [(q["question_id"], q.get("part") or q["number"]) for q in data["questions"]]
    rows = []
    for st in sorted(data["students"], key=lambda s: s["user_code"]):
        for qid, num in qorder:
            p = st["parts"].get(qid, {})
            rows.append({"email": st["email"], "StudentId": st["user_code"], "uid": st["uid"],
                         "name": st["name"], "batch": st["batch"], "Question_ID": qid,
                         "Part": num, "code": p.get("code", "")})
    return rows


def write_submission_tab(slug: str, rows: list[dict], sheet_id: str = None, rebuild: bool = False) -> dict:
    """ADDITIVE write to the tab named exactly `slug` in the Outcomes master sheet.
    - New tab: create it, write header + all rows.
    - Existing tab: APPEND only rows whose (StudentId, Question_ID) is not already
      present. The original Metabase rows, any OPs-appended late submissions, and any
      grading columns are NEVER cleared or overwritten. Submissions are frozen once
      the window closes, so a re-sync only adds genuinely new rows.
    - rebuild=True: the question structure changed (e.g. an MCQ was dropped or the
      question_id→Part mapping was corrected), so CLEAR the tab and rewrite header +
      all rows fresh. Stale rows and any grading columns from the old structure go away
      (they were built against the wrong parts and must be regraded anyway)."""
    import urllib.parse
    import requests
    from grader.results_sheet import _token
    from grader.pending_sheet import SHEET_ID, read_rows
    sheet_id = sheet_id or SHEET_ID
    H = {"Authorization": f"Bearer {_token()}"}
    base = f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}"
    meta = requests.get(base + "?fields=sheets.properties(sheetId,title)", headers=H, timeout=30)
    meta.raise_for_status()
    titles = {s["properties"]["title"]: s["properties"]["sheetId"] for s in meta.json().get("sheets", [])}

    def _key(r):
        return (str(r.get("StudentId", "")).strip(), str(r.get("Question_ID", "")).strip())

    # Google Sheets rejects a cell over 50,000 chars, and the whole batch write
    # fails atomically on even ONE such cell - not just that row. A student who
    # pastes raw code directly into the LMS answer box (instead of a repo link)
    # can produce a 'code' cell in the hundreds of thousands of characters, which
    # silently blocked EVERY student's submission from ever reaching the sheet
    # (confirmed live on iitp-aimlt-2601-81259: 23 of 3045 cells over the limit,
    # up to 287K chars, zero rows written for any of the 435 students as a
    # result). Truncate defensively with a visible marker rather than crash.
    _CELL_CAP = 49000

    def _cap(v):
        s = "" if v is None else str(v)
        if len(s) <= _CELL_CAP:
            return v if v is None else s
        return s[:_CELL_CAP] + f"…[TRUNCATED for Sheets' 50k cell limit - {len(s)} chars total]"

    def _vals(rs):
        return [[_cap(r.get(c)) for c in SUB_COLS] for r in rs]

    if slug not in titles:                                  # fresh tab → header + everything
        rr = requests.post(base + ":batchUpdate", headers=H, timeout=30,
                           json={"requests": [{"addSheet": {"properties": {"title": slug}}}]})
        rr.raise_for_status()
        gid = rr.json()["replies"][0]["addSheet"]["properties"]["sheetId"]
        rng = urllib.parse.quote(f"'{slug}'!A1")
        requests.put(base + f"/values/{rng}?valueInputOption=RAW", headers=H, timeout=180,
                     json={"values": [SUB_COLS] + _vals(rows)}).raise_for_status()
        return {"tab": slug, "added": len(rows), "kept": 0, "total": len(rows),
                "url": f"https://docs.google.com/spreadsheets/d/{sheet_id}/edit#gid={gid}"}

    if rebuild:                                             # structure changed → clear tab + rewrite fresh
        gid = titles[slug]
        clr = urllib.parse.quote(f"'{slug}'")
        requests.post(base + f"/values/{clr}:clear", headers=H, timeout=60).raise_for_status()
        rng = urllib.parse.quote(f"'{slug}'!A1")
        requests.put(base + f"/values/{rng}?valueInputOption=RAW", headers=H, timeout=180,
                     json={"values": [SUB_COLS] + _vals(rows)}).raise_for_status()
        return {"tab": slug, "added": len(rows), "kept": 0, "total": len(rows), "rebuilt": True,
                "url": f"https://docs.google.com/spreadsheets/d/{sheet_id}/edit#gid={gid}"}

    gid = titles[slug]                                      # existing tab → append new only
    # Defensive: a tab can exist with NO header row - e.g. an earlier run's
    # single header+data PUT failed partway (confirmed live: an oversized cell
    # 400'd that call before the header ever landed, leaving a real, named tab
    # that read_rows() then silently misparses - it treats row 1's actual
    # STUDENT data as column names, so every downstream read comes back
    # garbage (1 "student", no real Question_IDs) with no error anywhere.
    # Check the raw first row against SUB_COLS and backfill the header if it's
    # missing, before trusting read_rows()'s parse of "existing" at all.
    raw_row1 = read_rows(sheet_id, slug, raw_first_row=True) if False else None
    hdr_rng = urllib.parse.quote(f"'{slug}'!A1:{_col_letter(len(SUB_COLS))}1")
    hdr_resp = requests.get(base + f"/values/{hdr_rng}", headers=H, timeout=30)
    hdr_resp.raise_for_status()
    first_row = (hdr_resp.json().get("values") or [[]])[0]
    if first_row != SUB_COLS:
        requests.post(base + ":batchUpdate", headers=H, timeout=30, json={"requests": [
            {"insertDimension": {"range": {"sheetId": gid, "dimension": "ROWS",
                                           "startIndex": 0, "endIndex": 1}, "inheritFromBefore": False}}]}
                     ).raise_for_status()
        rng = urllib.parse.quote(f"'{slug}'!A1")
        requests.put(base + f"/values/{rng}?valueInputOption=RAW", headers=H, timeout=60,
                     json={"values": [SUB_COLS]}).raise_for_status()

    existing = read_rows(sheet_id, slug)
    have = {_key(r) for r in existing}
    new_rows = [r for r in rows if _key(r) not in have]
    if new_rows:
        rng = urllib.parse.quote(f"'{slug}'!A1")
        requests.post(base + f"/values/{rng}:append?valueInputOption=RAW&insertDataOption=INSERT_ROWS",
                      headers=H, timeout=180, json={"values": _vals(new_rows)}).raise_for_status()
    return {"tab": slug, "added": len(new_rows), "kept": len(existing),
            "total": len(existing) + len(new_rows),
            "url": f"https://docs.google.com/spreadsheets/d/{sheet_id}/edit#gid={gid}"}


def create_submission_sheet(slug: str, progress=None) -> dict:
    """End-to-end: fetch the assignment from Metabase → set up the eval's 4 parts
    (embedded rubric → criteria, per-part problem statement, question_id map,
    normalize total→#questions) → write the lean submission tab (= slug)."""
    log = progress or (lambda m: None)
    from app.db.session import get_session
    from app.db import repo
    from app.db.models import Part
    s = get_session()
    ev = repo.get_eval(s, slug)
    if not ev:
        raise ValueError(f"no eval '{slug}'")
    lms = (ev.config_json or {}).get("pending", {}).get("eval_id")
    if not lms:
        raise ValueError("no LMS eval_id in the eval's pending config")
    old_qmap = (ev.config_json or {}).get("metabase", {}).get("question_map") or {}
    log(f"5|fetching LMS {lms} from Metabase…")
    data = fetch_assignment(lms)
    qs, studs = data["questions"], data["students"]
    skipped = data.get("skipped", [])
    skip_note = f" · skipped {len(skipped)} non-subjective ({', '.join(sk['type'] for sk in skipped)})" if skipped else ""
    log(f"30|{len(qs)} subjective questions · {len(studs)} students{skip_note} · building parts…")

    old_part_keys = {p.key for p in repo.parts(s, ev.id)}
    for old in repo.parts(s, ev.id):
        s.delete(old)
    s.flush()
    from grader.intake import parse_rubric
    gen_used, ps_all, rub_all, q_meta = [], [], [], []
    for q in qs:
        ps = q["problem_statement"]
        pnum = q.get("part") or q["number"]                 # TRUE part number (body header), else row number
        ptitle = q.get("part_title") or _part_title(ps) or f"Part {pnum}"
        # AUTHORITATIVE per-part max = the paper's declared marks. For this LMS type that is
        # exactly Metabase's per-question correct_question_score (q['lms_max'] = 30/30/25/15),
        # and it also appears in the '## Part N … (M marks)' header. We DON'T trust lms_max
        # blindly, though: some LMS types normalise correct_question_score to 1 per question
        # while the rubric is out of 25 — so the header / embedded-rubric total is the real
        # grading scale and lms_max is only the normalisation target. Priority:
        #   embedded '## Rubric' table  →  header '(M marks)'  →  task-line sum  →  lms_max  →  25.
        crits = parse_rubric_table(q["rubric_md"])          # embedded '## Rubric' table, if the question ships one
        if not crits:
            crits = _ps_tasks(ps)                           # else the '**Task N.x — … (M marks)**' lines
        csum = round(sum(m for _, m in crits), 2) if crits else 0.0
        pmax = _header_marks(ps) or (csum or None) or float(q.get("lms_max") or 0) or 25.0
        if not crits or abs(csum - pmax) > 0.5:             # no breakdown, or it doesn't reconcile to the part max
            gen_used.append(q["number"])
            crits = [("Overall quality and completeness against the task and its acceptance criteria", pmax)]
        mx = round(pmax, 2)
        label = f"Part {pnum} — {ptitle}"
        rub_all.append("\n".join([f"## part{pnum} — {ptitle} ({mx:g})"] + [f"- {c} — {m:g}" for c, m in crits]))
        ps_all.append(f"# {label}\n\n{ps}")
        q_meta.append({"question_id": q["question_id"], "ps": ps, "lms_max": q.get("lms_max", 1.0)})

    # Derive part keys through the SAME parse_rubric the setup-save uses, so a later
    # rubric re-save can never desync Part.key from the download folders / question_map.
    rubric_md = "\n\n".join(rub_all)
    part_defs = parse_rubric(rubric_md)
    if len(part_defs) != len(q_meta):
        raise ValueError(f"rubric parsed into {len(part_defs)} parts but there are {len(q_meta)} questions")
    qmap, part_max, lms_max, total = {}, {}, {}, 0.0
    for pd, qm in zip(part_defs, q_meta):                   # aligned by order
        s.add(Part(eval_id=ev.id, key=pd["key"], title=pd["title"], max_marks=pd["max_marks"],
                   order=pd["order"], breakdown_json=pd["breakdown"], problem_statement=qm["ps"]))
        qmap[qm["question_id"]] = pd["key"]
        part_max[pd["key"]] = pd["max_marks"]
        lms_max[qm["question_id"]] = qm["lms_max"]
        total += pd["max_marks"]

    lms_total = sum(lms_max.values()) or 1.0                # LMS grand total (NOT assumed = #questions)
    ev.total_marks = int(round(total))
    ev.normalize_to = int(round(lms_total)) or 1            # grade /total → normalize to the LMS total
    ev.rubric_md = rubric_md                                # so the setup page shows the auto-filled rubric
    ev.problem_statement_md = "\n\n---\n\n".join(ps_all)[:60000]
    cfg = dict(ev.config_json or {})
    cfg["metabase"] = {"lms_id": int(lms), "question_map": qmap,
                       "part_max": part_max, "lms_max": lms_max, "lms_total": round(lms_total, 2)}
    cfg["submissions_tab"] = slug
    # If the PART STRUCTURE changed (e.g. a hand-edited rubric had split the 4 question
    # parts into 19 task-level parts, or an MCQ was dropped), the atomised rubric and any
    # existing grades were built against the OLD parts and are now inconsistent with the
    # question_map + submission tab. Clear them so grading rebuilds cleanly at the true
    # part granularity (one score per question part, matching the sheet's rows).
    new_part_keys = set(qmap.values())
    if old_part_keys and old_part_keys != new_part_keys:
        from sqlalchemy import delete as _sa_delete
        from app.db.models import Grade, Student as _St
        cfg.pop("atomized", None)
        studs_db = repo.students(s, ev.id)
        sids = [st.id for st in studs_db]
        if sids:
            s.execute(_sa_delete(Grade).where(Grade.student_id.in_(sids)))
        for st in studs_db:                                 # let graded students re-grade at the new parts
            if st.grade_status == "graded":
                st.grade_status = "not_submitted" if st.submission_type == "empty" else "pending"
                st.total_score = None
        log(f"35|part structure changed ({len(old_part_keys)}→{len(new_part_keys)}) · "
            f"cleared atomised rubric + stale grades")
    ev.config_json = cfg
    ev.status = "setup"
    s.commit()

    # If the question set or the question_id→Part mapping changed vs a prior fetch
    # (e.g. an MCQ dropped, or parts re-keyed to the correct part), the old tab rows are
    # wrong — rebuild the tab fresh. Otherwise stay additive (preserves late submissions).
    rebuild = bool(old_qmap) and old_qmap != qmap
    log(f"70|writing submission sheet ({'REBUILD — structure changed' if rebuild else 'additive'})…")
    res = write_submission_tab(slug, submission_rows(data), rebuild=rebuild)
    log(f"100|ready · {'rebuilt ' if res.get('rebuilt') else '+'}{res['added']} rows / {res['total']} total · {len(qs)} parts · "
        f"grade /{ev.total_marks} → LMS /{ev.normalize_to}")
    return {"lms_id": int(lms), "questions": len(qs), "students": len(studs),
            "added": res["added"], "kept": res["kept"], "rows": res["total"],
            "url": res["url"], "tab": slug, "total_marks": ev.total_marks,
            "normalize_to": ev.normalize_to, "generated_rubric_parts": gen_used,
            "skipped": skipped}


def qc_submission_sheet(slug: str) -> dict:
    """Validate that the written submission tab is correct BEFORE we spend money
    grading it. Cross-checks the sheet against the eval's parts + the live Metabase
    counts. Returns {ok, issues, warnings, stats}."""
    from collections import Counter
    from app.db.session import get_session
    from app.db import repo
    from grader.pending_sheet import read_rows, SHEET_ID
    s = get_session()
    ev = repo.get_eval(s, slug)
    if not ev:
        return {"ok": False, "issues": [f"no eval '{slug}'"], "warnings": [], "stats": {}}
    cfg = ev.config_json or {}
    qmap = cfg.get("metabase", {}).get("question_map", {})
    valid_qids = set(qmap)
    tab = cfg.get("submissions_tab", slug)
    issues, warnings = [], []

    try:
        rows = read_rows(SHEET_ID, tab)
    except Exception as e:
        return {"ok": False, "issues": [f"cannot read tab '{tab}': {e}"], "warnings": [], "stats": {}}

    header_ok = rows and set(SUB_COLS).issubset(rows[0].keys())
    if not header_ok:
        issues.append(f"missing expected columns {SUB_COLS}")
    n_q = len(valid_qids) or 4

    bad_qid = [r for r in rows if valid_qids and r.get("Question_ID") not in valid_qids]
    if bad_qid:
        issues.append(f"{len(bad_qid)} row(s) have a Question_ID not in this eval's 4 questions")
    bad_code = [r for r in rows if r.get("code", "").strip()
                and not r["code"].strip().startswith("https://github.com/")]
    if bad_code:
        warnings.append(f"{len(bad_code)} row(s) have a non-github, non-blank code link")
    no_email = sum(1 for r in rows if not r.get("email", "").strip())
    no_uid = sum(1 for r in rows if not str(r.get("uid", "")).strip())
    if no_email:
        issues.append(f"{no_email} row(s) missing email")
    if no_uid:
        issues.append(f"{no_uid} row(s) missing uid")
    dups = [k for k, n in Counter((r.get("StudentId"), r.get("Question_ID")) for r in rows).items() if n > 1]
    if dups:
        issues.append(f"{len(dups)} duplicate (student, question) pair(s)")
    per = Counter(r.get("StudentId") for r in rows)
    over = [sid for sid, n in per.items() if n > n_q]
    if over:
        issues.append(f"{len(over)} student(s) have more than {n_q} rows")

    # link/part naming alignment — a strong signal that qid->part mapping is right
    mis = 0
    for r in rows:
        code, part = r.get("code", "").lower(), str(r.get("Part", ""))
        if code and part and f"part{part}" in code.replace("-", "").replace("_", ""):
            pass
        elif code and part and _re.search(rf"part\s*([1-9])", code):
            if _re.search(rf"part\s*([1-9])", code).group(1) != part:
                mis += 1
    if mis:
        warnings.append(f"{mis} row(s): repo name's part number disagrees with the Part column")

    gh = sum(1 for r in rows if r.get("code", "").startswith("https://github.com/"))
    blank = sum(1 for r in rows if not r.get("code", "").strip())
    stats = {"rows": len(rows), "students": len(per), "questions_expected": n_q,
             "github_links": gh, "blank_code": blank, "distinct_question_ids": len({r.get("Question_ID") for r in rows})}
    return {"ok": not issues, "issues": issues, "warnings": warnings, "stats": stats}


GRADE_COLS = ["Score", "MaxMarks", "answerScore", "Feedback"]


def _col_letter(n: int) -> str:
    s = ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def write_grades_to_sheet(slug: str, sheet_id: str = None) -> dict:
    """One eval, one sheet: write the grading columns (Score, MaxMarks, answerScore,
    Feedback) into the SAME submission tab, aligned to each (StudentId, Question_ID)
    row. answerScore = (part score / part max) × that question's LMS max, so the LMS
    can sum per-question scores to its own total. The Feedback column carries the
    student's report-card link (one card holds all four parts' feedback, so the same
    link is written on every part row); it falls back to the text feedback only when
    no card exists. Only the grading columns are written; the submission columns and
    row order are untouched."""
    import urllib.parse
    import requests
    from grader.results_sheet import _token
    from grader.pending_sheet import SHEET_ID, read_rows
    from app.db.session import get_session
    from app.db import repo
    sheet_id = sheet_id or SHEET_ID
    s = get_session()
    ev = repo.get_eval(s, slug)
    if not ev:
        raise ValueError(f"no eval '{slug}'")
    mb = (ev.config_json or {}).get("metabase", {})
    qmap = mb.get("question_map", {})                       # question_id -> part_key
    lms_max = mb.get("lms_max", {})                         # question_id -> LMS max
    part_max = mb.get("part_max", {})                       # part_key -> rubric max

    grade = {}                                              # (user_code, part_key) -> Grade
    card_url = {}                                           # user_code -> report card link
    stotal, sgraded = {}, set()                             # per-student total + graded set
    for st in repo.students(s, ev.id):
        for g in repo.grades_for(s, st.id):
            grade[(st.student_code, g.part_key)] = g
        url = ((st.download_meta_json or {}).get("report") or {}).get("url")
        if url:
            card_url[st.student_code] = url
        if st.grade_status == "graded":
            sgraded.add(st.student_code); stotal[st.student_code] = st.total_score or 0

    tm = ev.total_marks or 1
    sub_tab = (ev.config_json or {}).get("submissions_tab") or slug
    rows = read_rows(sheet_id, sub_tab)

    # Which column actually holds the ID we grade by (Student.student_code)?
    # NOT always "StudentId" - `load_submissions`/`detect_columns` picks whichever
    # column looks most ID-like at ingest time (e.g. the numeric Metabase 'uid'
    # instead of a roll-number-style 'StudentId'), so a push that hardcodes
    # "StudentId" can silently match ZERO rows if a different column was actually
    # used (confirmed live on iimsi-dm-2511-81161: 'uid' matched all 60 rows,
    # 'StudentId' matched none - every score/link came back blank). Pick whichever
    # candidate column actually matches known student codes for THIS sheet.
    known_codes = {st.student_code for st in repo.students(s, ev.id)}
    id_col = "StudentId"
    if rows:
        candidates = [c for c in ("uid", "StudentId", "User Code", "code") if c in rows[0]]
        id_col = max(candidates or ["StudentId"],
                     key=lambda c: sum(1 for r in rows if str(r.get(c, "")).strip() in known_codes))

    # Opt-in flag set by load_submissions' shared_repo_all_parts (see there): ONE
    # LMS question's single link gets replicated onto every rubric part, so a real
    # Part-1 Grade row exists for THIS question_id even though the question really
    # represents the whole multi-part submission. Without this, the branch below
    # would report ONLY that one part's score/max rescaled to the LMS's full max -
    # silently dropping every other part's marks from what syncs back as the
    # official grade (confirmed live on iitp-sdaieng-2602-81303: a student whose
    # real, correct total was 94/100 got reported as 93.3333, Part 1 alone).
    shared_repo = bool(mb.get("shared_repo_all_parts"))

    out, graded_n = [], 0
    for r in rows:
        code = str(r.get(id_col, "")).strip()
        qid = str(r.get("Question_ID", "")).strip()
        pk = qmap.get(qid)
        g = grade.get((code, pk)) if pk else None
        if g is not None and g.feedback and shared_repo:    # one combined submission -> report the TOTAL
            score_out, pm = stotal.get(code, g.score), tm
            lm = float(lms_max.get(qid, 1.0))
            ascore = round((score_out / pm) * lm, 1) if pm else 0.0
            fb = card_url.get(code) or g.feedback
            out.append([score_out, pm, ascore, fb]); graded_n += 1
        elif g is not None and g.feedback:                  # per-question: this row's own part grade
            pm = g.max or part_max.get(pk) or 1
            lm = float(lms_max.get(qid, 1.0))
            ascore = round((g.score / pm) * lm, 1) if pm else 0.0
            # Feedback column carries the report-card link — one card holds every
            # part's feedback, so each part row for a student gets the same link.
            fb = card_url.get(code) or g.feedback
            out.append([g.score, g.max, ascore, fb]); graded_n += 1
        elif code in sgraded:                              # COMBINED eval: one submission graded
            lm = float(lms_max.get(qid, 1.0))              # against a multi-part rubric -> write the
            tot = stotal.get(code, 0)                      # student TOTAL to this single question row
            ascore = round((tot / tm) * lm, 1) if tm else 0.0
            out.append([tot, tm, ascore, card_url.get(code) or ""]); graded_n += 1
        else:
            out.append(["", "", "", ""])                   # not graded / no submission

    H = {"Authorization": f"Bearer {_token()}"}
    base = f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}"
    start = _col_letter(len(SUB_COLS) + 1)                  # first grading column (I)
    rng = urllib.parse.quote(f"'{sub_tab}'!{start}1")       # write INTO the submission tab (not a new tab)
    requests.put(base + f"/values/{rng}?valueInputOption=RAW", headers=H, timeout=180,
                 json={"values": [GRADE_COLS] + out}).raise_for_status()
    meta = requests.get(base + "?fields=sheets.properties(sheetId,title)", headers=H, timeout=30).json()
    gid = next((sh["properties"]["sheetId"] for sh in meta.get("sheets", [])
                if sh["properties"]["title"] == sub_tab), 0)
    return {"tab": sub_tab, "graded": graded_n, "rows": len(rows), "id_col": id_col,
            "url": f"https://docs.google.com/spreadsheets/d/{sheet_id}/edit#gid={gid}"}


COST_TAB = "API Cost Log"
COST_HEADER = ["Timestamp", "Eval", "Student", "Part", "Model", "Input tok", "Output tok",
               "Cache read tok", "Cache write tok", "Cost USD", "Cost INR"]


REGRADE_TAB = "Regrade Cost Log"
REGRADE_HEADER = ["Timestamp", "Eval", "Student", "Source", "Model", "n", "Total (of max)",
                  "Input tok", "Output tok", "Cache read tok", "Cache write tok", "Cost USD", "Cost INR"]


def log_regrade_cost(slug: str, code: str, source: str, mdl: str, n: int,
                     cost: dict, total: float = None, sheet_id: str = None):
    """Append ONE row per re-grade (ticket / manual) to the 'Regrade Cost Log' tab —
    each dispute regrade is billed individually, so it gets its own line. Best-effort."""
    if not cost:
        return
    import urllib.parse
    import requests
    from grader.results_sheet import _token
    from grader.pending_sheet import SHEET_ID
    import datetime as _dt
    sheet_id = sheet_id or SHEET_ID
    row = [_dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), slug, code, source, mdl, n,
           total if total is not None else "", cost.get("in", 0), cost.get("out", 0),
           cost.get("cache_read", 0), cost.get("cache_write", 0),
           round(cost.get("usd", 0), 5), round(cost.get("inr", 0), 3)]
    H = {"Authorization": f"Bearer {_token()}"}
    base = f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}"
    meta = requests.get(base + "?fields=sheets.properties(title)", headers=H, timeout=30).json()
    if REGRADE_TAB not in [sh["properties"]["title"] for sh in meta.get("sheets", [])]:
        requests.post(base + ":batchUpdate", headers=H, timeout=30,
                      json={"requests": [{"addSheet": {"properties": {"title": REGRADE_TAB}}}]}).raise_for_status()
        requests.put(base + f"/values/{urllib.parse.quote(chr(39)+REGRADE_TAB+chr(39)+'!A1')}?valueInputOption=RAW",
                     headers=H, timeout=60, json={"values": [REGRADE_HEADER]}).raise_for_status()
    rng = urllib.parse.quote(f"'{REGRADE_TAB}'!A1")
    requests.post(base + f"/values/{rng}:append?valueInputOption=RAW&insertDataOption=INSERT_ROWS",
                  headers=H, timeout=60, json={"values": [row]}).raise_for_status()


def ensure_cost_tab(sheet_id: str = None):
    """Create the 'API Cost Log' tab with its header row if it doesn't exist yet.
    Idempotent and cheap; safe to call once before a grading run."""
    import requests
    from grader.results_sheet import _token
    from grader.pending_sheet import SHEET_ID
    sheet_id = sheet_id or SHEET_ID
    H = {"Authorization": f"Bearer {_token()}"}
    base = f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}"
    meta = requests.get(base + "?fields=sheets.properties(sheetId,title)", headers=H, timeout=30).json()
    titles = [sh["properties"]["title"] for sh in meta.get("sheets", [])]
    if COST_TAB not in titles:
        import urllib.parse
        requests.post(base + ":batchUpdate", headers=H, timeout=30,
                      json={"requests": [{"addSheet": {"properties": {"title": COST_TAB}}}]}).raise_for_status()
        rng = urllib.parse.quote(f"'{COST_TAB}'!A1")
        requests.put(base + f"/values/{rng}?valueInputOption=RAW", headers=H, timeout=60,
                     json={"values": [COST_HEADER]}).raise_for_status()
    gid = next((sh["properties"]["sheetId"] for sh in requests.get(
        base + "?fields=sheets.properties(sheetId,title)", headers=H, timeout=30).json().get("sheets", [])
        if sh["properties"]["title"] == COST_TAB), 0)
    return f"https://docs.google.com/spreadsheets/d/{sheet_id}/edit#gid={gid}"


EVAL_COST_TAB = "API Cost Log per Eval"
EVAL_COST_HEADER = ["Timestamp", "Eval", "Title", "Run type", "Model", "Students", "Submissions",
                    "Graded", "Input tok", "Output tok", "Cache read tok", "Cache write tok",
                    "Cost USD", "Cost INR", "Avg INR/student"]


def write_eval_cost_summary(slug: str, title: str, run: dict, sheet_id: str = None) -> dict:
    """Append ONE summary row for a completed run to the 'API Cost Log per Eval'
    tab (created if missing). Called from the dashboard finalize button."""
    import urllib.parse
    import requests
    from grader.results_sheet import _token
    from grader.pending_sheet import SHEET_ID
    sheet_id = sheet_id or SHEET_ID
    t = run["totals"]
    row = [run.get("at", ""), slug, title or "", run.get("type", ""), run.get("model", ""),
           run.get("n_students", 0), run.get("n_submissions", 0), run.get("n_graded", 0),
           t["in"], t["out"], t["cache_read"], t["cache_write"], round(t["usd"], 4),
           round(t["inr"], 2), run.get("avg_inr", 0)]
    H = {"Authorization": f"Bearer {_token()}"}
    base = f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}"
    meta = requests.get(base + "?fields=sheets.properties(sheetId,title)", headers=H, timeout=30).json()
    titles = [sh["properties"]["title"] for sh in meta.get("sheets", [])]
    if EVAL_COST_TAB not in titles:
        requests.post(base + ":batchUpdate", headers=H, timeout=30,
                      json={"requests": [{"addSheet": {"properties": {"title": EVAL_COST_TAB}}}]}).raise_for_status()
        rng = urllib.parse.quote(f"'{EVAL_COST_TAB}'!A1")
        requests.put(base + f"/values/{rng}?valueInputOption=RAW", headers=H, timeout=60,
                     json={"values": [EVAL_COST_HEADER]}).raise_for_status()
    rng = urllib.parse.quote(f"'{EVAL_COST_TAB}'!A1")
    requests.post(base + f"/values/{rng}:append?valueInputOption=RAW&insertDataOption=INSERT_ROWS",
                  headers=H, timeout=60, json={"values": [row]}).raise_for_status()
    gid = next((sh["properties"]["sheetId"] for sh in requests.get(
        base + "?fields=sheets.properties(sheetId,title)", headers=H, timeout=30).json().get("sheets", [])
        if sh["properties"]["title"] == EVAL_COST_TAB), 0)
    return {"tab": EVAL_COST_TAB, "url": f"https://docs.google.com/spreadsheets/d/{sheet_id}/edit#gid={gid}"}


def clear_cost_log_for_eval(slug: str, sheet_id: str = None) -> int:
    """Remove this eval's per-call detail rows from the 'API Cost Log' tab, keeping
    the header and every other eval's rows. Rewrites the tab in place."""
    import urllib.parse
    import requests
    from grader.results_sheet import _token
    from grader.pending_sheet import SHEET_ID
    sheet_id = sheet_id or SHEET_ID
    H = {"Authorization": f"Bearer {_token()}"}
    base = f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}"
    rng = urllib.parse.quote(f"'{COST_TAB}'!A1:K100000")
    vals = requests.get(base + f"/values/{rng}", headers=H, timeout=60).json().get("values", [])
    if not vals:
        return 0
    header, body = vals[0], vals[1:]
    kept = [r for r in body if not (len(r) > 1 and r[1] == slug)]
    removed = len(body) - len(kept)
    if not removed:
        return 0
    requests.post(base + f"/values/{rng}:clear", headers=H, timeout=60).raise_for_status()
    requests.put(base + f"/values/{urllib.parse.quote(chr(39)+COST_TAB+chr(39)+'!A1')}?valueInputOption=RAW",
                 headers=H, timeout=120, json={"values": [header] + kept}).raise_for_status()
    return removed


def append_cost_rows(rows: list, sheet_id: str = None):
    """Append one or more rows to the 'API Cost Log' tab IMMEDIATELY (used for
    per-API-call instant logging). Best-effort: the caller wraps this so a sheet
    hiccup never breaks grading. Assumes ensure_cost_tab() ran first."""
    import urllib.parse
    import requests
    from grader.results_sheet import _token
    from grader.pending_sheet import SHEET_ID
    sheet_id = sheet_id or SHEET_ID
    H = {"Authorization": f"Bearer {_token()}"}
    base = f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}"
    rng = urllib.parse.quote(f"'{COST_TAB}'!A1")
    requests.post(base + f"/values/{rng}:append?valueInputOption=RAW&insertDataOption=INSERT_ROWS",
                  headers=H, timeout=60, json={"values": rows}).raise_for_status()


def write_cost_log_to_sheet(slug: str, sheet_id: str = None, ts: str = None) -> dict:
    """Append the authoritative run SUMMARY (TOTAL + AVG/student) to the 'API Cost
    Log' tab at the end of a grading run, computed from each student's recorded
    download_meta_json['cost']. Per-call detail rows are appended live during grading
    (instant logging); this adds the reconciled totals even if some live appends were
    throttled. Real billed tokens and INR, not an estimate."""
    import urllib.parse
    import requests
    from grader.results_sheet import _token
    from grader.pending_sheet import SHEET_ID
    from app.db.session import get_session
    from app.db import repo
    sheet_id = sheet_id or SHEET_ID
    s = get_session()
    ev = repo.get_eval(s, slug)
    if not ev:
        raise ValueError(f"no eval '{slug}'")
    if not ts:
        import datetime as _dt
        ts = _dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    nst = 0
    tot = {"calls": 0, "in": 0, "out": 0, "cr": 0, "cw": 0, "usd": 0.0, "inr": 0.0}
    for st in repo.students(s, ev.id):
        c = (st.download_meta_json or {}).get("cost")
        if not c:
            continue
        nst += 1
        tot["calls"] += c.get("calls", 0); tot["in"] += c.get("in", 0); tot["out"] += c.get("out", 0)
        tot["cr"] += c.get("cache_read", 0); tot["cw"] += c.get("cache_write", 0)
        tot["usd"] += c.get("usd", 0); tot["inr"] += c.get("inr", 0)
    if not nst:
        return {"logged": 0, "note": "no cost data recorded on any student (grade first)"}
    rows = [
        [ts, slug, f"— TOTAL ({nst} students)", "", "", tot["in"], tot["out"], tot["cr"],
         tot["cw"], round(tot["usd"], 4), round(tot["inr"], 2)],
        [ts, slug, "— AVG / student", "", "", round(tot["in"] / nst), round(tot["out"] / nst),
         round(tot["cr"] / nst), round(tot["cw"] / nst), round(tot["usd"] / nst, 5),
         round(tot["inr"] / nst, 2)],
    ]
    ensure_cost_tab(sheet_id)
    append_cost_rows(rows, sheet_id)
    H = {"Authorization": f"Bearer {_token()}"}
    base = f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}"
    meta = requests.get(base + "?fields=sheets.properties(sheetId,title)", headers=H, timeout=30).json()
    gid = next((sh["properties"]["sheetId"] for sh in meta.get("sheets", [])
                if sh["properties"]["title"] == COST_TAB), 0)
    return {"logged": nst, "total_inr": round(tot["inr"], 2),
            "avg_inr": round(tot["inr"] / nst, 2), "total_usd": round(tot["usd"], 4),
            "tab": COST_TAB, "url": f"https://docs.google.com/spreadsheets/d/{sheet_id}/edit#gid={gid}"}
