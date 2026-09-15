"""Dataset grounding for grading.

Many evaluations give students an EXTERNAL dataset (usually a Google-Drive folder
of CSV/Excel files) that the answers must be derived from. Without it the grader
can only judge a submission's internal consistency — it can't tell that a claimed
"12% churn / 8,234 rows / 5 duplicates" is fabricated. This module closes that gap.

Design — **analyse once, reuse the context**:
  1. detect_links()   — find candidate dataset links in the problem statement
  2. download once     — into data/<slug>/dataset/ (service-account downloader)
  3. profile()         — factual profile straight from the files (schema, real row
                         counts, distributions, sample rows, join keys)
  4. build_brief()     — a compact, token-budgeted markdown brief
The brief is stored on the eval and injected into EVERY grading + feedback call
(identical for all students → cheap + fair). Raw files are never sent per call.

Optional `reference_findings()` drafts expected answers for the VERIFIABLE parts
— but only enters grading after the user reviews it (see rubric-ethics posture).
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Optional

# ── detection ────────────────────────────────────────────────────────────────
_DATA_EXT = (".csv", ".tsv", ".xlsx", ".xls", ".parquet", ".json", ".zip", ".gz")
_DATA_WORDS = re.compile(
    r"\b(data\s?set|dataset|raw data|\.csv|\.xlsx|excel|spreadsheet|"
    r"customers?\.csv|orders?\.csv|the data|provided data|sample data)\b", re.I)


def detect_links(problem_statement: str) -> list[dict]:
    """Candidate dataset links in the paper. Returns [{raw, type, why}] — a link
    is flagged when it looks like a data file OR sits near dataset-ish wording.
    The user confirms before anything downloads (not every link is a dataset)."""
    from grader.download import classify, _URL
    if not problem_statement:
        return []
    text = problem_statement
    seen, out = set(), []
    for m in _URL.finditer(text):
        url = m.group(0).rstrip(").,]}>\"'")
        if url in seen:
            continue
        seen.add(url)
        stype, _ = classify(url)
        if stype in ("github", "gdoc"):
            continue                                   # submission/reference, not data
        low = url.lower()
        window = text[max(0, m.start() - 120): m.end() + 120]
        is_file = any(low.endswith(e) or e + "?" in low or e in low for e in _DATA_EXT)
        near = bool(_DATA_WORDS.search(window))
        if stype in ("drive_folder", "drive_file", "s3", "url") and (is_file or near):
            why = ("looks like a data file" if is_file
                   else "near dataset wording in the paper")
            out.append({"raw": url, "type": stype, "why": why})
    return out


# ── profiling ────────────────────────────────────────────────────────────────
MAX_STAT_ROWS = 300_000          # cap rows read for stats on very large files
SAMPLE_ROWS = 5
TOP_CATS = 6
TABULAR = {".csv", ".tsv", ".txt", ".xlsx", ".xls", ".parquet", ".json"}


def _read(path: Path, nrows: Optional[int] = None):
    import pandas as pd
    ext = path.suffix.lower()
    if ext in (".csv", ".txt"):
        return pd.read_csv(path, engine="python", on_bad_lines="skip", nrows=nrows)
    if ext == ".tsv":
        return pd.read_csv(path, sep="\t", engine="python", on_bad_lines="skip", nrows=nrows)
    if ext in (".xlsx", ".xls"):
        return pd.read_excel(path, nrows=nrows)         # first sheet
    if ext == ".parquet":
        return pd.read_parquet(path)
    if ext == ".json":
        return pd.read_json(path)
    return None


def _fast_rowcount(path: Path) -> Optional[int]:
    if path.suffix.lower() not in (".csv", ".tsv", ".txt"):
        return None
    try:
        with path.open("rb") as f:
            n = sum(buf.count(b"\n") for buf in iter(lambda: f.read(1 << 20), b""))
        return max(0, n - 1)                            # minus header
    except Exception:
        return None


def _col_summary(series) -> dict:
    import pandas as pd
    s = series
    nulls = int(s.isna().sum())
    nunique = int(s.nunique(dropna=True))
    out = {"name": str(s.name), "dtype": str(s.dtype), "nulls": nulls, "n_unique": nunique}
    if pd.api.types.is_numeric_dtype(s):
        d = s.dropna()
        if len(d):
            out["min"], out["max"] = _num(d.min()), _num(d.max())
            out["mean"] = _num(round(float(d.mean()), 3))
    elif pd.api.types.is_datetime64_any_dtype(s):
        d = s.dropna()
        if len(d):
            out["min"], out["max"] = str(d.min()), str(d.max())
    else:
        vc = s.dropna().astype(str).value_counts().head(TOP_CATS)
        total = max(1, int(s.dropna().shape[0]))
        out["top"] = [{"value": k[:40], "pct": round(v * 100 / total, 1)} for k, v in vc.items()]
    return out


def _num(v):
    try:
        f = float(v)
        return int(f) if f.is_integer() else round(f, 3)
    except (TypeError, ValueError):
        return str(v)


def profile(dataset_dir: str | Path) -> dict:
    """Factual profile of every tabular file under dataset_dir — computed straight
    from the data. This is hard ground truth, not an LLM guess."""
    dataset_dir = Path(dataset_dir)
    files = sorted(p for p in dataset_dir.rglob("*")
                   if p.is_file() and p.suffix.lower() in TABULAR)
    tables, skipped = [], []
    for p in files:
        if p.stat().st_size > 100 * 1024 * 1024:        # >100MB → sample for stats
            df, sampled = _read(p, nrows=MAX_STAT_ROWS), True
            total = _fast_rowcount(p)
        else:
            df, sampled, total = _read(p), False, None
        if df is None or df.shape[1] == 0:
            skipped.append(p.name); continue
        rows = total if (sampled and total is not None) else int(df.shape[0])
        cols = [_col_summary(df[c]) for c in df.columns]
        try:
            sample = df.head(SAMPLE_ROWS).astype(str).to_dict(orient="records")
        except Exception:
            sample = []
        tables.append({"file": str(p.relative_to(dataset_dir)), "rows": rows,
                       "n_cols": int(df.shape[1]), "sampled": sampled,
                       "columns": cols, "sample": sample})
    return {"tables": tables, "skipped": skipped, "join_keys": _join_keys(tables)}


def _join_keys(tables: list[dict]) -> list[dict]:
    """Columns shared across files (likely relationships) — id-ish names first."""
    from collections import defaultdict
    where = defaultdict(list)
    for t in tables:
        for c in t["columns"]:
            where[c["name"]].append(t["file"])
    out = []
    for name, fs in where.items():
        if len(fs) > 1:
            out.append({"column": name, "files": fs,
                        "id_like": bool(re.search(r"(^|_)id$|_id\b|key$", name, re.I))})
    out.sort(key=lambda x: (not x["id_like"], x["column"]))
    return out


# ── compact brief (what actually goes into every grading call) ───────────────
def build_brief(prof: dict, budget: int = 30000) -> str:
    """Compact markdown brief from a profile — token-budgeted for per-call reuse.
    Budget is generous enough that a multi-file dataset (e.g. a 4-part capstone)
    is never silently dropped file-by-file; with prompt caching the brief is a
    one-time cost across a run."""
    if not prof or not prof.get("tables"):
        return ""
    L = ["The student solved the problem using THIS dataset. Verify their numbers,",
         "counts and claims against it; treat these figures as ground truth.", ""]
    for t in prof["tables"]:
        star = " (stats from a sample)" if t.get("sampled") else ""
        L.append(f"### {t['file']} — {t['rows']:,} rows × {t['n_cols']} cols{star}")
        for c in t["columns"]:
            bits = [c["dtype"]]
            if c["nulls"]:
                bits.append(f"{c['nulls']} nulls")
            if "min" in c:
                bits.append(f"range {c['min']}–{c['max']}" + (f", mean {c['mean']}" if "mean" in c else ""))
            elif c.get("top"):
                bits.append("top: " + ", ".join(f"{v['value']} {v['pct']}%" for v in c["top"][:4]))
            if c.get("n_unique") is not None and "min" not in c:
                bits.append(f"{c['n_unique']} distinct")
            L.append(f"- **{c['name']}** ({'; '.join(bits)})")
        if t.get("sample"):
            cols = list(t["sample"][0].keys())
            L.append("\nsample rows:")
            L.append(" | ".join(cols))
            for r in t["sample"]:
                L.append(" | ".join(str(r.get(c, ""))[:24] for c in cols))
        L.append("")
    if prof.get("join_keys"):
        L.append("### Likely relationships (shared columns)")
        for j in prof["join_keys"][:8]:
            L.append(f"- `{j['column']}` in {', '.join(j['files'])}")
    brief = "\n".join(L)
    if len(brief) > budget:
        brief = brief[:budget] + "\n[... dataset brief truncated to fit budget ...]"
    return brief


# ── optional: reviewable reference findings (verifiable expected answers) ─────
_REF_SYS = (
    "You are given a dataset profile (real schema + statistics), the assignment's "
    "problem statement, and its rubric. Produce a SHORT list of VERIFIABLE ground-"
    "truth facts a grader can check a student's work against — only things that "
    "have a single correct answer computable from the data (e.g. exact row counts, "
    "actual duplicate counts, real class balance / churn rate, correct join keys, "
    "columns with missing values and how many). Do NOT prescribe one 'correct' "
    "answer for open-ended tasks (hypotheses, strategy, model choice) — those have "
    "many valid solutions; note them as 'open-ended, judge on reasoning'. Be "
    "concise and factual; if you are unsure of a value, say so rather than guess."
)


def reference_findings(brief: str, problem_statement: str, rubric_md: str,
                       model: str = "claude-opus-4-8") -> str:
    """Draft expected findings for the verifiable parts. REVIEW before using —
    an LLM can be wrong, and a wrong reference would mis-grade."""
    from grader.rubric_ai import _client
    msg = _client().with_options(timeout=300.0).messages.create(
        model=model, max_tokens=16000, thinking={"type": "adaptive"}, system=_REF_SYS,
        messages=[{"role": "user", "content":
                   f"DATASET PROFILE:\n{brief}\n\nPROBLEM STATEMENT:\n{problem_statement[:30000]}"
                   f"\n\nRUBRIC:\n{rubric_md[:8000]}\n\nCover EVERY dataset file/part shown in the "
                   f"profile above (part1, part2, part3, …), not just the first. List the verifiable "
                   f"ground-truth facts, grouped per part."}])
    return "".join(b.text for b in msg.content if getattr(b, "type", None) == "text").strip()


# ── orchestration: download once → profile → store the brief on the eval ─────
def ingest(eval_id: int, links: list[str], progress=None) -> dict:
    """Download the confirmed dataset link(s) once, profile them, and store the
    compact brief on the eval (config_json['dataset']). Re-runs replace cleanly."""
    from app.db import repo
    from app.db.session import eval_data_dir, get_session
    from grader.download import _fetch_one, _sa_key_path
    log = progress or (lambda m: None)
    links = [l for l in (links or []) if l.strip()]
    if not links:
        raise ValueError("no dataset link provided")
    s = get_session()
    ev = repo.get_eval_by_id(s, eval_id)
    ddir = eval_data_dir(ev.slug) / "dataset"
    if ddir.exists():
        shutil.rmtree(ddir)
    ddir.mkdir(parents=True, exist_ok=True)
    sa = _sa_key_path(ev.config_json or {})
    log(f"10|downloading {len(links)} dataset link(s)…")
    fetched = []
    for i, raw in enumerate(links, 1):
        dest = ddir if len(links) == 1 else ddir / f"link{i}"
        fetched.append(_fetch_one(sa, raw, dest))
    log("55|profiling dataset files…")
    prof = profile(ddir)
    brief = build_brief(prof)
    ds = dict((ev.config_json or {}).get("dataset", {}))
    ds.update({
        "links": links, "download": fetched,
        "n_tables": len(prof["tables"]),
        "n_rows": sum(t["rows"] for t in prof["tables"]),
        "tables": [{"file": t["file"], "rows": t["rows"], "n_cols": t["n_cols"],
                    "sampled": t.get("sampled", False)} for t in prof["tables"]],
        "skipped": prof["skipped"], "join_keys": prof["join_keys"], "brief": brief,
    })
    meta = dict(ev.config_json or {})
    meta["dataset"] = ds
    ev.config_json = meta
    s.commit()
    log(f"100|profiled {ds['n_tables']} file(s), {ds['n_rows']:,} rows")
    return {"n_tables": ds["n_tables"], "n_rows": ds["n_rows"], "tables": ds["tables"],
            "skipped": ds["skipped"], "brief": brief,
            "download": [{"status": f["status"], "n_files": f.get("n_files", 0)} for f in fetched]}


def save_reference(eval_id: int, reference_md: str, approved: bool) -> None:
    """Persist the (user-reviewed) reference findings + whether they enter grading."""
    from app.db import repo
    from app.db.session import get_session
    s = get_session()
    ev = repo.get_eval_by_id(s, eval_id)
    meta = dict(ev.config_json or {})
    ds = dict(meta.get("dataset", {}))
    ds["reference_md"] = (reference_md or "").strip()
    ds["reference_approved"] = bool(approved) and bool(ds["reference_md"])
    meta["dataset"] = ds
    ev.config_json = meta
    s.commit()
