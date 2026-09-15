#!/usr/bin/env python3
"""
Stage 1 — Clean the raw submission sheet into a structured, one-row-per-student CSV.

Reads ``<run>/config.yaml`` for the sheet layout and part mapping, then writes
``<run>/submissions_clean.csv``.

Handles two sheet layouts:
  - long : one row per (student, question); question_id_col maps to a part.
  - wide : one row per student; each part has its own answer column.

For every answer it classifies the submission:
  github        — a proper https://github.com/<owner>/<repo> URL
  partial_path  — looked like owner/repo, expanded to a full URL
  pages_url     — a GitHub Pages / other web URL (not a source repo)
  inline_text   — the answer was pasted as text (no link)
  empty         — blank

Usage:
  python -m grader.clean_submissions --run runs/<slug>
"""

import argparse
import csv
import re
from collections import defaultdict

from grader.config import add_run_arg, load_config


# A pages/other URL only needs to be recorded, not graded, so 200 chars is plenty.
# Inline text IS the submission and must be preserved for grading (a pasted
# portfolio can run to tens of thousands of chars).
PAGES_URL_MAX_CHARS = 200
INLINE_TEXT_MAX_CHARS = 90000


def strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text or "").strip()


def extract_url(raw: str) -> tuple[str, str]:
    """Return (value, status). See module docstring for status meanings."""
    text = strip_html(raw).strip()
    if not text:
        return "", "empty"

    m = re.search(r"https?://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)", text)
    if m:
        return f"https://github.com/{m.group(1)}/{m.group(2)}", "github"

    m = re.match(r"^([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)\s*$", text)
    if m:
        return f"https://github.com/{m.group(1)}/{m.group(2)}", "partial_path"

    if re.search(r"https?://", text):
        return text[:PAGES_URL_MAX_CHARS], "pages_url"

    return text[:INLINE_TEXT_MAX_CHARS], "inline_text"


def _collate(students: dict, meta: dict, part_keys: list, extra_cols: list) -> list:
    collated = []
    for user, m in sorted(meta.items()):
        d = students[user]
        row = {"User Code": user}
        for col in extra_cols:
            row[col] = m.get(col, "")
        row["Submission Time"] = m.get("Submission Time", "")

        github_count = any_count = 0
        for p in part_keys:
            entry = d.get(p, {"url": "", "status": "empty"})
            row[p] = entry["url"]
            row[f"{p}_status"] = entry["status"]
            if entry["status"] in ("github", "partial_path"):
                github_count += 1
            if entry["status"] != "empty":
                any_count += 1
        row["Parts_With_GitHub"] = github_count
        row["Parts_Any_Answer"] = any_count
        collated.append(row)
    return collated


def run(run_dir: str):
    cfg = load_config(run_dir)
    sheet = cfg.sheet
    part_keys = cfg.part_keys
    layout = sheet.get("layout", "long")

    id_col = sheet["student_id_col"]
    extra_cols = sheet.get("extra_cols", [])
    time_col = sheet.get("time_col")

    if not cfg.paths.raw_csv.exists():
        raise SystemExit(f"ERROR: raw sheet not found: {cfg.paths.raw_csv}")

    with open(cfg.paths.raw_csv) as f:
        rows = list(csv.DictReader(f))

    students = defaultdict(dict)
    meta = {}

    if layout == "long":
        qmap = sheet["question_map"]
        qid_col = sheet["question_id_col"]
        answer_col = sheet["answer_col"]
        for r in rows:
            user = r[id_col]
            part = qmap.get(r.get(qid_col))
            if part is None:
                continue  # a question not mapped to any graded part (e.g. an intro)
            url, status = extract_url(r.get(answer_col, ""))
            students[user][part] = {"url": url, "status": status}
            if user not in meta:
                meta[user] = {c: r.get(c, "") for c in extra_cols}
                meta[user]["Submission Time"] = r.get(time_col, "") if time_col else ""
    else:  # wide
        col_of = {p["key"]: p["column"] for p in cfg.parts}
        for r in rows:
            user = r[id_col]
            for pk in part_keys:
                url, status = extract_url(r.get(col_of[pk], ""))
                students[user][pk] = {"url": url, "status": status}
            meta[user] = {c: r.get(c, "") for c in extra_cols}
            meta[user]["Submission Time"] = r.get(time_col, "") if time_col else ""

    collated = _collate(students, meta, part_keys, extra_cols)

    fieldnames = (
        ["User Code"] + extra_cols + ["Submission Time"]
        + [c for p in part_keys for c in [p, f"{p}_status"]]
        + ["Parts_With_GitHub", "Parts_Any_Answer"]
    )
    with open(cfg.paths.clean_csv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(collated)

    # ── Summary ────────────────────────────────────────────────────────────────
    total = len(collated)
    n_parts = len(part_keys)
    all_gh = sum(1 for r in collated if r["Parts_With_GitHub"] == n_parts)
    partial = sum(1 for r in collated if 0 < r["Parts_With_GitHub"] < n_parts)
    inline_only = sum(1 for r in collated
                      if r["Parts_With_GitHub"] == 0 and r["Parts_Any_Answer"] > 0)
    no_sub = sum(1 for r in collated if r["Parts_Any_Answer"] == 0)

    print(f"Total students:                   {total}")
    print(f"Submitted all {n_parts} (GitHub links):   {all_gh}")
    print(f"Partial GitHub links:             {partial}")
    print(f"Inline text only (no GitHub):     {inline_only}")
    print(f"No submission at all:             {no_sub}\n")
    for p in part_keys:
        gh = sum(1 for r in collated if r[f"{p}_status"] in ("github", "partial_path"))
        other = sum(1 for r in collated if r[f"{p}_status"] in ("inline_text", "pages_url"))
        print(f"  {p}: {gh} GitHub links, {other} inline/other")

    print(f"\nClean CSV written: {cfg.paths.clean_csv}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Clean raw submission sheet")
    add_run_arg(ap)
    args = ap.parse_args()
    run(args.run)
