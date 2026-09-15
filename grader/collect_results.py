#!/usr/bin/env python3
"""
Stage 4 — Poll the batch until complete, parse the JSON grades, and write one
Markdown grading report per student plus a summary CSV.

Usage:
  python -m grader.collect_results --run runs/<slug>
  python -m grader.collect_results --run runs/<slug> --once   # check once, don't wait
"""

import argparse
import csv
import json
import os
import re
import sys
import time
from pathlib import Path

from grader.config import add_run_arg, load_config


# ── Report generator ──────────────────────────────────────────────────────────
def generate_report(cfg, user_code: str, grades: dict) -> str:
    lines = [f"# Grading Report: {user_code}", ""]
    earned = possible = 0
    for part_key in cfg.part_keys:
        rubric = cfg.rubrics[part_key]
        g = grades.get(part_key)
        lines.append("---")
        lines.append(f"## {rubric['title']} ({rubric['total_marks']} marks)")
        lines.append("")
        if g is None:
            lines.append("**Status:** Not submitted")
            lines.append("")
            possible += rubric["total_marks"]
            continue

        score = g.get("total_score", 0)
        max_score = g.get("total_max", rubric["total_marks"])
        earned += score
        possible += max_score

        lines.append(f"**Repository:** {g.get('repo_url', '')}")
        files = g.get("files_found", [])
        lines.append(f"**Files Found:** {', '.join(files) if files else 'None'}")
        lines.append(f"**Score: {score} / {max_score}**")
        lines.append("")

        breakdown = g.get("breakdown", {})
        if breakdown:
            lines += ["### Score Breakdown", "", "| Component | Score | Feedback |", "|---|---|---|"]
            for comp, d in breakdown.items():
                if isinstance(d, dict):
                    s = d.get("score", 0)
                    m = d.get("max", "?")
                    c = d.get("comment", "").replace("|", "/").replace("\n", " ")
                    lines.append(f"| {comp} | {s}/{m} | {c} |")
            lines.append("")

        if g.get("note"):
            lines += [f"> **Note:** {g['note']}", ""]
        if g.get("overall_feedback"):
            lines += ["### Feedback", "", g["overall_feedback"], ""]

    lines.append("---")
    lines.append(f"## Overall Score: {earned} / {possible}")
    lines.append("")
    if possible > 0:
        lines.append(f"**Percentage: {round(earned / possible * 100, 1)}%**")
    lines.append("")
    return "\n".join(lines)


def parse_ai_response(cfg, text: str, part_key: str, meta: dict) -> dict:
    rubric = cfg.rubrics[part_key]
    total = rubric["total_marks"]
    try:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            raise ValueError("No JSON object found in response")
        data = json.loads(m.group(0))
        data["repo_url"] = meta.get("repo_url", "")
        data["files_found"] = meta.get("files_found", [])
        if meta.get("note"):
            data["note"] = meta["note"]
        return data
    except Exception as e:
        return _zero(rubric, total, f"Error parsing grader response: {e}\n\nRaw:\n{text[:500]}", meta)


def _zero(rubric, total, feedback, meta, comment="not gradeable") -> dict:
    return {
        "total_score": 0, "total_max": total,
        "breakdown": {k: {"score": 0, "max": v, "comment": comment}
                      for k, v in rubric["breakdown"].items()},
        "overall_feedback": feedback,
        "repo_url": meta.get("repo_url", ""),
        "files_found": meta.get("files_found", []),
    }


def run(run_dir: str, once: bool = False):
    cfg = load_config(run_dir)
    _require_api_key()
    import anthropic

    if not cfg.paths.batch_state.exists():
        sys.exit(f"ERROR: {cfg.paths.batch_state} not found. Run grade_batch first.")

    state = json.loads(cfg.paths.batch_state.read_text())
    batch_id = state["batch_id"]
    request_meta = state["request_meta"]
    student_rows = state["student_rows"]

    client = anthropic.Anthropic()
    cfg.paths.reports_dir.mkdir(parents=True, exist_ok=True)

    interval = 30
    while True:
        batch = client.messages.batches.retrieve(batch_id)
        c = batch.request_counts
        print(f"Batch {batch_id[:20]}...  status={batch.processing_status}  "
              f"processing={c.processing} succeeded={c.succeeded} "
              f"errored={c.errored} expired={c.expired}")
        if batch.processing_status == "ended":
            break
        if once:
            print("Batch still in progress. Run again later.")
            return
        print(f"  Waiting {interval}s...")
        time.sleep(interval)
        interval = min(interval + 15, 120)

    print("\nCollecting results...")
    ai_grades = {}
    for result in client.messages.batches.results(batch_id):
        meta = request_meta.get(result.custom_id, {})
        uc, pk = meta.get("user_code"), meta.get("part_key")
        if not uc or not pk:
            print(f"  WARNING: unknown custom_id {result.custom_id}")
            continue
        rubric = cfg.rubrics[pk]
        total = rubric["total_marks"]
        if result.result.type == "succeeded":
            text = result.result.message.content[0].text.strip()
            grade = parse_ai_response(cfg, text, pk, meta)
        elif result.result.type == "errored":
            grade = _zero(rubric, total, f"API error during grading: {result.result.error}",
                          meta, comment="API error")
        else:
            grade = _zero(rubric, total, f"Request was {result.result.type}.",
                          meta, comment=f"Request {result.result.type}")
        ai_grades.setdefault(uc, {})[pk] = grade

    # Merge pre-resolved skips
    for cid, meta in request_meta.items():
        if not meta.get("skip"):
            continue
        pk = meta["part_key"]
        rubric = cfg.rubrics[pk]
        ai_grades.setdefault(meta["user_code"], {})[pk] = {
            "total_score": meta.get("total_score", 0),
            "total_max": meta.get("total_max", rubric["total_marks"]),
            "breakdown": {k: {"score": 0, "max": v, "comment": meta.get("skip_reason", "not gradeable")}
                          for k, v in rubric["breakdown"].items()},
            "overall_feedback": meta.get("overall_feedback", "Not gradeable."),
            "repo_url": meta.get("repo_url", ""),
            "files_found": [],
        }

    print(f"\nWriting reports for {len(student_rows)} students...")
    summary = []
    for uc, row in sorted(student_rows.items()):
        grades = ai_grades.get(uc, {})
        (cfg.paths.reports_dir / f"{uc}.md").write_text(generate_report(cfg, uc, grades))

        earned = sum(g.get("total_score", 0) for g in grades.values())
        possible = sum(g.get("total_max", cfg.part_max[pk]) for pk, g in grades.items())
        for pk in cfg.part_keys:
            if pk not in grades:
                possible += cfg.part_max[pk]
        pct = round(earned / possible * 100, 1) if possible else 0.0

        entry = {"User Code": uc}
        for col in cfg.sheet.get("extra_cols", []):
            entry[col] = row.get(col, "")
        entry["Parts_Submitted"] = row.get("Parts_Any_Answer", "")
        for pk in cfg.part_keys:
            entry[f"{pk}_Score"] = grades.get(pk, {}).get("total_score", 0)
        entry.update({"Total_Earned": earned, "Total_Possible": possible, "Percentage": pct})
        summary.append(entry)
        print(f"  {uc}: {earned}/{possible} ({pct}%)")

    if summary:
        with open(cfg.paths.summary_csv, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(summary[0].keys()))
            w.writeheader()
            w.writerows(summary)

    print(f"\nDone.\nReports: {cfg.paths.reports_dir}/\nSummary: {cfg.paths.summary_csv}")


def _require_api_key():
    _env = Path(".env")
    if _env.exists():
        for line in _env.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())
    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("ERROR: ANTHROPIC_API_KEY not set.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Collect batch results and write reports")
    add_run_arg(ap)
    ap.add_argument("--once", action="store_true", help="Check status once and exit")
    args = ap.parse_args()
    run(args.run, once=args.once)
