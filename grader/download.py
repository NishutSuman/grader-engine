"""Unified, DB-driven submission downloader — consolidates the 5 diverged
per-run downloaders into one dispatcher.

Submission types handled: Google Drive folder/file, Google Doc (export→PDF),
GitHub repo (clone), S3 / direct URL, inline pasted text. Google Drive uses a
**service account** (authenticated → bypasses the per-IP anti-abuse throttle;
see the drive-download-service-account learning). Parallel + resumable.

Threads do I/O only and return results; the DB is written from the main thread
(SQLite dislikes cross-thread writers)."""
from __future__ import annotations

import re
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable, Optional

import requests

from app.db import repo
from app.db.models import Student
from app.db.session import eval_data_dir, get_session

# ── link classification ─────────────────────────────────────────────────────
_HREF = re.compile(r'href=["\']([^"\']+)["\']', re.I)
_URL = re.compile(r'https?://[^\s"\'<>)]+')
FOLDER_RE = re.compile(r"/folders/([A-Za-z0-9_-]+)")
FILE_RE = re.compile(r"/file/d/([A-Za-z0-9_-]+)")
DOC_RE = re.compile(r"docs\.google\.com/document/d/([A-Za-z0-9_-]+)")
ID_RE = re.compile(r"[?&]id=([A-Za-z0-9_-]+)")
GH_RE = re.compile(r"github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)")
BASE = "https://www.googleapis.com/drive/v3/files"
GAPP = "application/vnd.google-apps"

_CODE_MARKERS = re.compile(r'(^|\n)\s*(import\s+\w|from\s+\w+\s+import|def\s+\w|class\s+\w|print\()')


def _looks_like_code(text: str) -> bool:
    """True when `text` contains actual source-code syntax (import/def/class/
    print(), at a line start) rather than plain prose. Used to tell apart two
    very different "text + URL" cells: (a) real pasted code that happens to
    reference a URL literal (e.g. an LLM API endpoint the code calls), where the
    CODE is the answer and the URL is incidental; vs (b) a cover-letter-style
    submission ("GitHub Repository: <url>. Module Location: /data_pipeline. The
    complete implementation includes: - Scraping scripts - SQLite DB ...") where
    a descriptive caption sits beside the link but the REPO is the actual answer
    and the prose is not a substitute deliverable. Both can easily exceed any
    fixed residual-length threshold, so length alone can't distinguish them -
    presence of code syntax can."""
    return bool(_CODE_MARKERS.search(text))


def _first_link(raw: str) -> str:
    """Pull the first real URL out of an HTML-wrapped or plain cell."""
    m = _HREF.search(raw)
    if m:
        return m.group(1)
    m = _URL.search(raw)
    return m.group(0) if m else ""


# scheme-less links (students often paste 'github.com/user/repo' with no https://,
# especially several in one cell) — without this only the first was ever cloned.
_BARE_HOST = re.compile(
    r"(?<![\w@/.:])((?:www\.)?(?:github\.com|gitlab\.com|bitbucket\.org|drive\.google\.com|"
    r"docs\.google\.com|colab\.research\.google\.com|huggingface\.co|1drv\.ms|onedrive\.live\.com)"
    r"/[^\s\"'<>)\]]+)", re.I)


def extract_links(raw: str) -> list[str]:
    """ALL distinct URLs in a cell, HTML-stripped and cleaned. Handles Metabase
    exports wrapped in <a href="…"> tags (optionally entity-encoded), plain URLs,
    scheme-less host links (github.com/…), and multiple links in one cell (newline
    / comma / <p> / space separated). Order-preserving, de-duplicated."""
    import html as _html
    if not raw:
        return []
    s = _html.unescape(str(raw))                     # &lt;a href=…&gt; / &amp; → real chars
    cands = [m.group(1) for m in _HREF.finditer(s)]           # href="…" targets
    cands += [m.group(0) for m in _URL.finditer(s)]           # http(s):// URLs
    cands += ["https://" + m.group(1) for m in _BARE_HOST.finditer(s)]  # bare host/path
    out, seen = [], set()
    for u in cands:
        u = u.strip().strip('<>"\'').rstrip('.,;)]}')  # trim tag/quote/punctuation debris
        if not u.startswith("http"):
            continue
        key = u.rstrip("/").lower()                  # dedup https vs bare form of the same link
        if key not in seen:
            seen.add(key)
            out.append(u)
    return out


def link_is_whole_answer(s: str, link: str, threshold: int = 300) -> bool:
    """True when `link` is essentially the entire cell `s` (a genuinely pasted
    submission link, optionally wrapped in thin <a>/<p> markup, plus perhaps a short
    caption like "(see part 2 for this — files: a.py, b.py)") rather than a URL
    embedded inside a substantial real text answer (e.g. "Github link to view
    document:- https://github.com/x/y <17000 chars of the actual answer>", or a
    scenario description like "the student opens http://sars.university.edu"
    followed by paragraphs of unrelated content). The threshold is deliberately well
    above a plausible short caption (~100-150 chars) but far below every genuine
    swallowed-answer case seen in practice (all 9,000+ chars) — short captions
    alongside a real link submission must not be misread as "the real answer lives
    in the text, not the link". Removes the WHOLE anchor tag wrapping the link —
    attributes AND its visible display text — not just the bare URL string: a pasted
    GitHub link is very often wrapped as `<a href="https://github.com/x/y">x/y</a>`,
    whose visible label duplicates the repo name/path and is textually different
    from the href value, so naively subtracting only the URL string would leave that
    label behind and wrongly look like "real prose remains" for an ordinary link."""
    s2 = re.sub(r'<a\b[^>]*href=["\']' + re.escape(link) + r'["\'][^>]*>.*?</a>',
                " ", s, flags=re.S | re.I)
    s2 = re.sub(r"<[^>]+>", " ", s2).replace(link, "")
    return len(s2.strip()) < threshold


def classify(raw: str) -> tuple[str, str]:
    """Return (submission_type, value). value is an id/url/text depending on type."""
    s = (raw or "").strip()
    if not s or s.lower() in ("not attempted", "na", "n/a"):
        return ("empty", "")
    link = _first_link(s)
    # A student can legitimately CITE a github/drive/doc link inside a real structured-
    # text answer (e.g. "Github repository link to view document:- https://github.com/…"
    # followed by the actual multi-paragraph answer). Every specific-pattern branch below
    # used to fire on ANY match found anywhere in the cell regardless of how much real
    # text surrounded it, so a citation link silently made the whole real answer get
    # replaced by "go clone this repo" — losing the answer even when the repo turned out
    # to have nothing gradable in it. Only run the link-pattern branches at all when the
    # link is essentially the WHOLE cell; a mere citation falls through to inline text.
    #
    # link_is_whole_answer()'s length check alone missed a real, opposite-direction
    # case: a cover-letter-style cell ("GitHub Repository: <url>. Module Location:
    # /data_pipeline. The complete implementation includes: - Scraping scripts -
    # SQLite DB - SQL queries...") sits ABOVE its 300-char default (confirmed live:
    # real cases ran 750-960 chars) but is still just a caption describing where the
    # actual work lives, not a second deliverable - the link is still the real
    # submission regardless of how long that caption runs. This is THE canonical
    # classification point every download path ultimately routes through
    # (_fetch_one() calls this directly on the raw stored text) - confirmed live: 15
    # students in one eval had a genuine GitHub link silently swallowed into "inline
    # text" this way, and NONE of their repos were ever downloaded
    # (download_status='ok' the whole time - failed completely silently). Presence of
    # actual code syntax in the residual (after removing the link) is what actually
    # tells the two cases apart, not length - see _looks_like_code().
    if link and (link_is_whole_answer(s, link) or not _looks_like_code(s.replace(link, " "))):
        hay = link
        if GH_RE.search(hay):
            return ("github", "https://github.com/" + GH_RE.search(hay).group(1).removesuffix(".git"))
        if DOC_RE.search(hay):
            return ("gdoc", DOC_RE.search(hay).group(1))
        if FOLDER_RE.search(hay):
            return ("drive_folder", FOLDER_RE.search(hay).group(1))
        if FILE_RE.search(hay) or ID_RE.search(hay):
            m = FILE_RE.search(hay) or ID_RE.search(hay)
            return ("drive_file", m.group(1))
        if "amazonaws.com" in hay or hay.startswith("s3://"):
            # keep the WHOLE url — S3 filenames may contain spaces (".../SAHIBA ASSIGNMENT.pdf")
            # and _first_link stops at whitespace, chopping it into a dead key. Spaces are
            # %-encoded at fetch time (requote_uri in _fetch_url).
            single = s.startswith("http") and s.count("http") == 1 and "\n" not in s and "<" not in s
            return ("s3", s if single else hay)
        if "1drv.ms" in hay or "onedrive" in hay:
            return ("onedrive", hay)
        return ("url", link)
    # No link and not empty → treat as pasted inline answer (HTML or text). This
    # used to require len(s) > 200, which silently dropped short structured-text
    # answers (e.g. a Task 3.1/3.2a response that happens to be brief) as if the
    # student had submitted nothing at all — there's no length below which plain
    # text stops being a real answer, so any non-empty non-link text qualifies.
    # A cell that's HTML markup with no real text content (e.g. "<p><br></p>") is
    # still a genuine non-submission, not an inline answer — check the tag-stripped
    # content, not just the raw string, before falling back to "empty".
    if not re.sub(r"<[^>]+>", "", s).strip():
        return ("empty", "")
    return ("inline", s)


# ── Google Drive (service account) ──────────────────────────────────────────
_local = threading.local()
_creds = None
_credlock = threading.Lock()
SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]


def _sa_key_path(config: dict) -> Path:
    """Locate the service-account JSON: config override, else repo glob."""
    p = config.get("drive_sa_key")
    if p and Path(p).exists():
        return Path(p)
    repo_root = Path(__file__).resolve().parent.parent
    for cand in list(repo_root.rglob("oauth-practice-*.json")) + list(repo_root.rglob("sa_key.json")):
        return cand
    raise FileNotFoundError("No service-account key found (config 'drive_sa_key' or oauth-practice-*.json)")


def _token(sa_key: Path) -> str:
    global _creds
    from google.auth.transport.requests import Request as GRequest
    from google.oauth2 import service_account
    with _credlock:
        if _creds is None:
            _creds = service_account.Credentials.from_service_account_file(str(sa_key), scopes=SCOPES)
        if not _creds.valid:
            _creds.refresh(GRequest())
        return _creds.token


def _get(sa_key, url, params, stream=False, tries=4):
    for i in range(tries):
        try:
            r = requests.get(url, params=params, stream=stream, timeout=(10, 120),
                             headers={"Authorization": f"Bearer {_token(sa_key)}"})
            if r.status_code == 200:
                return r
            if r.status_code in (401, 403, 429, 500, 503) and i < tries - 1:
                time.sleep(3 * (i + 1)); continue
            return r
        except requests.RequestException:
            if i < tries - 1:
                time.sleep(3 * (i + 1)); continue
            raise
    return r


def _safe(n: str) -> str:
    return re.sub(r"[^\w.\- ]", "_", n)[:120]


def _list_folder(sa_key, fid):
    for attempt in range(3):
        files, page = [], None
        while True:
            r = _get(sa_key, BASE, {"q": f"'{fid}' in parents and trashed=false",
                     "fields": "nextPageToken,files(id,name,mimeType)", "pageSize": 1000,
                     "pageToken": page or "", "supportsAllDrives": "true",
                     "includeItemsFromAllDrives": "true"})
            j = r.json() if r.status_code == 200 else {}
            files += j.get("files", [])
            page = j.get("nextPageToken")
            if not page:
                break
        if files or attempt == 2:
            return files
        time.sleep(3)
    return files


def _dl_file(sa_key, fid, name, mime, dest: Path):
    if mime.startswith(GAPP):
        r = _get(sa_key, f"{BASE}/{fid}/export", {"mimeType": "application/pdf"}, stream=True)
        name = (name or fid) + ".pdf"
    else:
        r = _get(sa_key, f"{BASE}/{fid}", {"alt": "media", "supportsAllDrives": "true"}, stream=True)
    if r.status_code != 200:
        raise RuntimeError(str(r.status_code))
    fp = dest / _safe(name)
    fp.write_bytes(r.content)
    return fp.name


def _fetch_drive_folder(sa_key, fid, dest: Path, depth=0, report=None):
    """Download every file in a Drive folder. Per-file failures are RECORDED in
    `report` (not swallowed) — a folder where some files fail must surface as
    `partial`, never as `ok`, or the student gets graded on an incomplete
    submission (this silently cost real students marks)."""
    report = report if report is not None else {"listed": 0, "errors": []}
    dest.mkdir(parents=True, exist_ok=True)
    m = _get(sa_key, f"{BASE}/{fid}", {"fields": "id,name"})
    if m.status_code == 404:
        raise RuntimeError("404 not-public (shared to specific accounts only)")
    listing = _list_folder(sa_key, fid)
    got = []
    for f in listing:
        if f["mimeType"].endswith(".folder") and depth < 2:
            got += _fetch_drive_folder(sa_key, f["id"], dest / _safe(f["name"]), depth + 1, report)
        else:
            report["listed"] += 1
            try:
                got.append(_dl_file(sa_key, f["id"], f["name"], f["mimeType"], dest))
            except Exception as e:
                report["errors"].append(f"{f.get('name', '?')}: {str(e)[:60]}")
    if listing and not got and report["errors"]:
        raise RuntimeError(f"all {len(report['errors'])} downloads failed")
    return got


def _fetch_drive_file(sa_key, fid, dest: Path):
    dest.mkdir(parents=True, exist_ok=True)
    r = _get(sa_key, f"{BASE}/{fid}", {"fields": "name,mimeType", "supportsAllDrives": "true"})
    meta = r.json() if r.status_code == 200 else {}
    if not meta:
        raise RuntimeError(f"file meta {r.status_code}")
    return [_dl_file(sa_key, fid, meta.get("name"), meta.get("mimeType", ""), dest)]


# ── other sources ───────────────────────────────────────────────────────────
def _list_files(root: Path) -> list[str]:
    """Relative paths of real files under root, excluding the .git dir."""
    out = []
    for p in root.rglob("*"):
        if p.is_file() and ".git/" not in str(p.relative_to(root)) + "/":
            out.append(str(p.relative_to(root)))
    return out


def _github_token() -> str:
    import os as _os
    tok = _os.environ.get("GITHUB_TOKEN")
    if tok:
        return tok
    envp = Path(__file__).resolve().parent.parent / ".env"
    if envp.exists():
        for line in envp.read_text().splitlines():
            line = line.strip()
            if line.startswith("GITHUB_TOKEN") and "=" in line:
                return line.split("=", 1)[1].strip()
    return ""


GIT_EVIDENCE_FILE = "GIT_EVIDENCE.txt"


def _fetch_git_evidence(url: str) -> str:
    """Branch + merge evidence via the GitHub API - NOT visible in our local
    clone, which is deliberately shallow + single-branch (--depth 1
    --single-branch, for speed/reliability) and therefore shows exactly 1
    commit and 0 feature branches REGARDLESS of what the student actually did.
    Confirmed live: a student with 2 real feature branches, both fully merged,
    9 real commits, got zeroed on a 3-mark git-workflow criterion because the
    grader had no way to see any of that. This restores real ground truth via
    a handful of lightweight API calls. Best-effort: returns '' on any failure
    (private repo, rate limit, network) so a submission still grades on its
    files even if this evidence can't be fetched."""
    m = GH_RE.search(url)
    if not m:
        return ""
    owner_repo = m.group(1).removesuffix(".git")
    tok = _github_token()
    H = {"Accept": "application/vnd.github+json"}
    if tok:
        H["Authorization"] = f"Bearer {tok}"
    base = f"https://api.github.com/repos/{owner_repo}"
    try:
        info = requests.get(base, headers=H, timeout=15).json()
        default = info.get("default_branch")
        if not default:
            return ""
        branches = requests.get(f"{base}/branches?per_page=100", headers=H, timeout=15).json()
        if not isinstance(branches, list):
            return ""
        names = [b["name"] for b in branches if isinstance(b, dict) and b.get("name")]
        others = [n for n in names if n != default]
        commits = requests.get(f"{base}/commits?sha={default}&per_page=100", headers=H, timeout=15).json()
        n_commits = len(commits) if isinstance(commits, list) else 0
        lines = [f"Default branch '{default}': {n_commits}{'+' if n_commits == 100 else ''} commit(s)."]
        if not others:
            lines.append("No other branches exist on the remote - only the default branch.")
        else:
            lines.append(f"Other branches on the remote: {', '.join(others)}")
            for br in others[:5]:                       # cap API calls per student
                cmp = requests.get(f"{base}/compare/{default}...{br}", headers=H, timeout=15).json()
                ahead, behind = cmp.get("ahead_by"), cmp.get("behind_by")
                if ahead is None:
                    continue
                status = ("fully merged into the default branch (0 commits remain unmerged)" if ahead == 0
                          else f"NOT fully merged - {ahead} commit(s) on this branch are not in '{default}'")
                lines.append(f"- '{br}': {status} (default is {behind} commit(s) ahead of this branch now).")
        return "\n".join(lines)
    except Exception:
        return ""


def _write_git_evidence(dest: Path, url: str) -> None:
    """Write GIT_EVIDENCE.txt into a cloned repo's folder, idempotently (skip
    if already fetched). Named/prioritized so it's never dropped by the
    grading budget cap - see grade._priority()."""
    ev_file = dest / "repo" / GIT_EVIDENCE_FILE
    if ev_file.exists():
        return
    evidence = _fetch_git_evidence(url)
    if evidence:
        try:
            ev_file.write_text(
                "=== GIT REPOSITORY EVIDENCE (fetched via GitHub API - the local clone below is "
                "shallow/single-branch and does NOT show real commit/branch history) ===\n\n" + evidence)
        except OSError:
            pass


def _fetch_github(url, dest: Path):
    dest.mkdir(parents=True, exist_ok=True)
    target = dest / "repo"
    if target.exists() and any(target.rglob("*")):
        _write_git_evidence(dest, url)
        return _list_files(target)
    import os as _os
    env = {**_os.environ, "GIT_TERMINAL_PROMPT": "0"}   # never hang on a private-repo auth prompt
    last = ""
    for attempt in range(2):                            # one retry for transient network
        r = subprocess.run(
            ["git", "clone", "--depth", "1", "--single-branch", "--no-tags", "--quiet",
             url, str(target)],
            capture_output=True, text=True, timeout=240, env=env)
        if r.returncode == 0:
            _write_git_evidence(dest, url)
            return _list_files(target)
        last = (r.stderr or "").strip()
        if "Authentication" in last or "not found" in last.lower() or "does not exist" in last.lower():
            break                                       # private/missing → don't retry
        subprocess.run(["rm", "-rf", str(target)])
    raise RuntimeError(f"clone failed: {last[:120]}")


def _blocked_ssrf_target(url: str) -> str:
    """Non-empty reason string if `url` resolves to somewhere a server-side
    fetch must never go: loopback, link-local (incl. the cloud metadata IP),
    private RFC1918 ranges, or a non-http(s) scheme. `url` here is a link a
    student pasted into their submission sheet — untrusted input reaching a
    server-side HTTP fetch is a textbook SSRF vector (e.g. a crafted link
    pointing at 169.254.169.254 to try to read cloud IAM credentials)."""
    import ipaddress
    import socket
    from urllib.parse import urlparse
    p = urlparse(url)
    if p.scheme not in ("http", "https"):
        return f"non-http(s) scheme {p.scheme!r}"
    host = p.hostname
    if not host:
        return "no host in URL"
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as e:
        return f"could not resolve host: {e}"
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            return f"resolves to a non-public address ({ip})"
    return ""


def _fetch_url(url, dest: Path):
    dest.mkdir(parents=True, exist_ok=True)
    from requests.utils import requote_uri
    from urllib.parse import urlparse, unquote
    import os as _os
    blocked = _blocked_ssrf_target(url)
    if blocked:
        raise RuntimeError(f"refusing to fetch — {blocked}")
    r = requests.get(requote_uri(url), timeout=(10, 90), allow_redirects=False)
    if r.status_code in (301, 302, 303, 307, 308):
        loc = r.headers.get("location", "")
        blocked = _blocked_ssrf_target(loc) if loc else "redirect with no Location"
        if blocked:
            raise RuntimeError(f"refusing to follow redirect — {blocked}")
        r = requests.get(requote_uri(loc), timeout=(10, 90))
    r.raise_for_status()  # %-encode spaces etc.
    body, ct = r.content, r.headers.get("content-type", "")
    # extension: the real filename in the URL is ground truth (S3 often serves
    # octet-stream, which mis-tagged everything as .bin); then content-type; then
    # magic bytes. Without this a real PDF lands as .bin and grading skips it.
    urlext = _os.path.splitext(unquote(urlparse(url).path))[1].lower()
    known = {".pdf", ".docx", ".doc", ".pptx", ".ppt", ".xlsx", ".csv", ".txt",
             ".png", ".jpg", ".jpeg", ".zip", ".ipynb", ".md", ".html"}
    if urlext in known:
        ext = urlext
    elif "pdf" in ct:
        ext = ".pdf"
    elif "word" in ct or "officedocument.wordprocessing" in ct:
        ext = ".docx"
    elif body[:5] == b"%PDF-":
        ext = ".pdf"
    elif body[:2] == b"PK":
        ext = ".docx"                                      # zip-based office doc
    else:
        ext = ".bin"
    (dest / f"submission{ext}").write_bytes(body)
    return [f"submission{ext}"]


def _fetch_inline(text, dest: Path):
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "inline_submission.html").write_text(text)
    return ["inline_submission.html"]


def _fetch(sa_key, stype, value, dest: Path, report=None) -> list[str]:
    if stype == "drive_folder":
        return _fetch_drive_folder(sa_key, value, dest, report=report)
    if stype in ("drive_file", "gdoc"):
        return _fetch_drive_file(sa_key, value, dest)
    if stype == "github":
        return _fetch_github(value, dest)
    if stype in ("s3", "url"):
        return _fetch_url(value, dest)
    if stype == "inline":
        return _fetch_inline(value, dest)
    if stype == "onedrive":
        raise RuntimeError("OneDrive link — not fetchable via API")
    return []


def _fetch_one(sa_key, raw: str, dest: Path) -> dict:
    """Download ONE submission link into dest. Returns a per-link result dict.
    A folder where SOME files failed is reported `partial` — never `ok` — so it is
    excluded from grading and surfaced in Download QC for a human decision."""
    stype, value = classify(raw)
    report = {"listed": 0, "errors": []}
    try:
        files = _fetch(sa_key, stype, value, dest, report)
        n, missed = len(files), len(report["errors"])
        status = "partial" if (n and missed) else ("ok" if n else "empty")
        out = {"raw": raw, "type": stype, "dir": str(dest), "status": status,
               "n_files": n, "n_pdfs": sum(1 for f in files if f.lower().endswith(".pdf"))}
        if report["listed"]:
            out["n_listed"] = report["listed"]          # expected vs fetched, for QC
        if missed:
            out["n_failed"] = missed
            out["errors"] = report["errors"][:5]
        return out
    except Exception as e:
        msg = str(e)[:150]
        status = ("restricted" if "404" in msg or "not found" in msg.lower()
                  else "not_gradeable" if "OneDrive" in msg
                  else "auth" if "Authentication" in msg else "failed")
        return {"raw": raw, "type": stype, "dir": str(dest), "status": status,
                "n_files": 0, "error": msg}


def _distinct_links(meta: dict) -> list[str]:
    """Distinct non-empty submission links for a student (single mode → 1)."""
    return list(dict.fromkeys(l["raw"] for l in (meta or {}).get("links", []) if l.get("raw")))


def _roll_up(results: list[dict]) -> str:
    """Per-student status from its per-link results. Anything short of 'every link
    fully fetched' is NOT ok — only `ok` students are graded."""
    if not results:
        return "not_submitted"
    kinds = {r["status"] for r in results}
    if kinds == {"ok"}:
        return "ok"
    if "partial" in kinds or "ok" in kinds:      # some content, but incomplete
        return "partial"
    if kinds == {"restricted"}:
        return "restricted"
    if kinds == {"empty"}:
        return "empty"
    return "failed"


def download_eval(eval_id: int, workers: int = 12, force: bool = False,
                  progress: Optional[Callable[[str], None]] = None) -> dict:
    """Download every link of every pending submission. Handles single- and
    per-part modes, parallel per-link, idempotent/resumable. Writes per-link +
    per-student status to the DB (main thread only)."""
    log = progress or (lambda m: None)
    s = get_session()
    ev = repo.get_eval_by_id(s, eval_id)
    sa_key = _sa_key_path(ev.config_json or {})
    dl_root = eval_data_dir(ev.slug) / "downloads"
    pending = repo.students_needing_download(s, eval_id, force=force)

    # Flatten to one task per (student, distinct link) for max parallelism.
    tasks = []                                          # (student_id, dest, raw)
    n_links_by_student = {}
    for st in pending:
        part_links = (st.download_meta_json or {}).get("part_links")
        if part_links:                                      # per-question: one folder per PART
            # DEDUP master repos: a student who pasted ONE repo into every part is cloned
            # ONCE (into the first part that carries that link), not once per part — that
            # was 4× the clone + 4× the files on disk (the "44 files" bug). Parts that
            # share the link read from this same folder at grade time (grade._perq_groups
            # scans the group's part folders and finds the one that holds the content).
            uniq: dict = {}                                 # normalized link -> owning part_key
            for pk, raw in part_links.items():
                k = (raw or "").strip()
                if k and k not in uniq:
                    uniq[k] = pk
            n_links_by_student[st.id] = len(uniq)
            for k, pk in uniq.items():
                tasks.append((st.id, dl_root / st.student_code / pk, k))
            continue
        links = _distinct_links(st.download_meta_json)
        if not links:
            st.download_status = "not_submitted"
            continue
        single = len(links) == 1
        n_links_by_student[st.id] = len(links)
        for i, raw in enumerate(links, 1):
            dest = dl_root / st.student_code if single else dl_root / st.student_code / f"link{i}"
            tasks.append((st.id, dest, raw))
    s.commit()

    n_students = len(n_links_by_student)
    log(f"downloading {len(tasks)} link(s) across {n_students} student(s) "
        f"(x{workers} workers, service-account + git)")
    if not tasks:
        return {}

    by_student: dict[int, list[dict]] = {}
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_fetch_one, sa_key, raw, dest): sid for sid, dest, raw in tasks}
        for fut in as_completed(futs):
            sid = futs[fut]
            by_student.setdefault(sid, []).append(fut.result())
            done += 1
            if done % 5 == 0 or done == len(tasks):
                log(f"{int(done * 100 / len(tasks))}|downloading {done}/{len(tasks)} links")

    # Write results per student (main thread). Every student that had tasks MUST
    # end on a terminal status — a student left at the default 'pending' is
    # invisible (grading needs 'ok', and nothing else looks at 'pending'), which
    # is exactly how interrupted runs silently dropped students.
    tally: dict[str, int] = {}
    for sid, results in by_student.items():
        st = s.get(Student, sid)
        expected = n_links_by_student.get(sid, 0)
        short = len(results) < expected             # some links never returned (run cut off)
        status = "incomplete" if short else _roll_up(results)
        meta = dict(st.download_meta_json or {})
        meta["download"] = {"status": status, "links": results,
                            "n_files": sum(r["n_files"] for r in results),
                            "n_ok": sum(1 for r in results if r["status"] == "ok"),
                            "n_expected": expected, "n_returned": len(results)}
        # annotate each stored link with its download outcome
        by_raw = {r["raw"]: r for r in results}
        for l in meta.get("links", []):
            r = by_raw.get(l.get("raw"))
            if r:
                l["dl_status"] = r["status"]
                l["local_dir"] = r["dir"]
        st.download_meta_json = meta
        st.download_status = status
        tally[status] = tally.get(status, 0) + 1
        if short:
            log(f"WARN {st.student_code}: only {len(results)}/{expected} links "
                f"returned — marked incomplete (not left pending)")

    # Sweep students that had tasks but produced NO results at all (their futures
    # never ran — e.g. the run was killed before reaching them). Surface as
    # 'incomplete' instead of leaving them stuck at 'pending'.
    for sid, expected in n_links_by_student.items():
        if sid in by_student:
            continue
        st = s.get(Student, sid)
        if not st or st.download_status not in (None, "", "pending", "downloading"):
            continue
        meta = dict(st.download_meta_json or {})
        meta["download"] = {"status": "incomplete", "links": [], "n_files": 0, "n_ok": 0,
                            "n_expected": expected, "n_returned": 0,
                            "note": "no download results — run interrupted before this "
                                    "student's links ran; re-run download"}
        st.download_meta_json = meta
        st.download_status = "incomplete"
        tally["incomplete"] = tally.get("incomplete", 0) + 1
        log(f"WARN {st.student_code}: no download results — marked incomplete (re-run download)")
    s.commit()
    log("100|done · " + ", ".join(f"{k}:{v}" for k, v in sorted(tally.items())))
    if ev.status in ("setup", "downloading"):
        ev.status = "downloaded"
        s.commit()
    return tally


# ── download QC (read-only cross-check — never re-downloads) ─────────────────
_QC_NOTE = {"restricted": "not shared / 404 — ask the student to re-share (anyone with link)",
            "failed": "download failed — retry or check the link",
            "auth": "private repo / auth required",
            "not_gradeable": "non-fetchable host (OneDrive etc.) — needs manual review",
            "empty": "downloaded but EMPTY (empty repo / no files)",
            "partial": "INCOMPLETE — some files in the folder failed to download; "
                       "re-download before grading (grading on this would under-score the student)",
            "incomplete": "download never finished for this student (run interrupted or "
                          "some links never returned) — re-run download before grading",
            "pending": "never downloaded — run download for this student",
            "downloading": "download in progress or interrupted — re-run download"}


def _count_files(d: Path) -> int:
    """Real gradeable files on disk under d, excluding the .git dir."""
    if not d or not d.exists():
        return 0
    return sum(1 for p in d.rglob("*")
               if p.is_file() and ".git/" not in str(p.relative_to(d)) + "/")


def download_qc(eval_id: int) -> dict:
    """Read-only audit of every downloaded submission: cross-checks each link's
    stored status AGAINST the files actually on disk. Flags empty/failed/
    restricted links and any link marked ok that has zero files on disk. Never
    downloads anything — pure verification."""
    s = get_session()
    ev = repo.get_eval_by_id(s, eval_id)
    dl_root = eval_data_dir(ev.slug) / "downloads"
    by_status: dict[str, int] = {}
    issues: list[dict] = []
    n_submitted = fully_ok = n_approved = n_files_total = 0
    for st in repo.students(s, eval_id):
        if st.submission_type == "empty":
            continue
        n_submitted += 1
        meta = st.download_meta_json or {}
        approved = bool((meta.get("download") or {}).get("qc_approved"))
        links = [l for l in meta.get("links", []) if l.get("raw")]
        # Resolve each link's on-disk dir. Prefer the stored local_dir, but fall
        # back to reconstructing it from student_code + part key — older download
        # runs never stored local_dir, and trusting only that field made QC
        # false-flag every student as empty (files were actually on disk).
        part_links = meta.get("part_links") or {}
        raw_to_dir = {raw: dl_root / st.student_code / pk for pk, raw in part_links.items()}
        single = not part_links and len(links) == 1
        probs = []
        for l in links:
            dls = l.get("dl_status") or st.download_status or "pending"
            by_status[dls] = by_status.get(dls, 0) + 1
            ld = l.get("local_dir")
            d = (Path(ld) if ld else raw_to_dir.get(l.get("raw"))
                 or (dl_root / st.student_code if single else None))
            n_disk = _count_files(d) if d else 0
            n_files_total += n_disk
            if dls == "ok" and n_disk == 0:
                probs.append({"link": l["raw"], "status": "empty_on_disk", "n_files": 0,
                              "note": "marked ok but 0 files on disk — re-download this link"})
            elif dls != "ok":
                probs.append({"link": l["raw"], "status": dls, "n_files": n_disk,
                              "note": _QC_NOTE.get(dls, dls)})
        # Student-level non-terminal state (never finished downloading) must
        # surface even when no per-link problem was recorded — otherwise an
        # interrupted student silently sits outside both grading and QC.
        if not probs and st.download_status not in ("ok",):
            probs.append({"link": "(all)", "status": st.download_status or "pending",
                          "n_files": sum(_count_files(Path(l["local_dir"]))
                                         for l in links if l.get("local_dir")),
                          "note": _QC_NOTE.get(st.download_status or "pending",
                                               f"download status = {st.download_status}")})
        if probs and approved:
            n_approved += 1                               # manually verified despite a bad link
        elif probs:
            issues.append({"code": st.student_code, "name": st.name or "",
                           "download_status": st.download_status,
                           "n_links": len(links), "n_ok": len(links) - len(probs),
                           "problems": probs})
        elif st.download_status == "ok" and links:
            fully_ok += 1
    issues.sort(key=lambda x: (x["n_ok"], x["code"]))     # worst (fewest ok) first
    return {"n_submitted": n_submitted, "fully_ok": fully_ok, "n_approved": n_approved,
            "with_issues": len(issues), "n_files_total": n_files_total,
            "by_status": by_status, "issues": issues}
