#!/usr/bin/env python3
"""
Stage 5 — Merge submission metadata + per-part scores + per-part feedback +
an auto-generated human remark into a single master CSV used by the PDF/report
generators.

Output: ``<run>/grading_master.csv``

Usage:
  python -m grader.build_master_csv --run runs/<slug>
"""

import argparse
import csv
import json
import re

from grader.config import add_run_arg, load_config


def parse_report(md: str, cfg) -> dict:
    """Extract per-part scores and feedback from a grading report .md."""
    result = {}
    for i, part_key in enumerate(cfg.part_keys, 1):
        title = f"Part {i}"
        score_m = re.search(
            rf"## {re.escape(title)}.*?\*\*Score: (\d+(?:\.\d+)?) / (\d+)\*\*", md, re.DOTALL)
        if score_m:
            result[f"{part_key}_score"] = float(score_m.group(1))
            result[f"{part_key}_max"] = int(score_m.group(2))
        else:
            result[f"{part_key}_score"] = 0
            result[f"{part_key}_max"] = cfg.part_max[part_key]

        section = re.search(rf"## {re.escape(title)}.*?(?=^## |\Z)", md, re.DOTALL | re.MULTILINE)
        if section:
            text = section.group(0)
            fb = re.search(r"### Feedback\s*\n+(.+?)(?=\n---|\n##|\Z)", text, re.DOTALL)
            if fb:
                feedback = re.sub(r"\n+", " ", fb.group(1).strip()).strip()
                feedback = re.sub(r"> \*\*Note:\*\* (.+?) ", r"[NOTE: \1] ", feedback)
                # Keep the full per-part paragraph; the report card renders it in full.
                result[f"{part_key}_feedback"] = feedback[:2000]
            else:
                result[f"{part_key}_feedback"] = ""
            if "**Status:** Not submitted" in text:
                result[f"{part_key}_feedback"] = "Not submitted"
                result[f"{part_key}_score"] = 0
        else:
            result[f"{part_key}_feedback"] = ""

    overall = re.search(r"## Overall Score: (\d+(?:\.\d+)?) / (\d+)", md)
    if overall:
        result["total_earned"] = float(overall.group(1))
        result["total_possible"] = int(overall.group(2))
        result["percentage"] = (round(result["total_earned"] / result["total_possible"] * 100, 1)
                                if result["total_possible"] else 0.0)
    else:
        result["total_earned"], result["total_possible"], result["percentage"] = 0, cfg.total_marks, 0.0
    return result


def part_status_label(row: dict, batch_meta: dict, user_code: str, part_key: str) -> str:
    """Friendly per-part status for the results sheet (e.g. 'Graded', 'Not submitted')."""
    status = row.get(f"{part_key}_status", "empty")
    meta = batch_meta.get(f"{user_code}__{part_key}", {})
    if status == "empty":
        return "Not submitted"
    if status == "pages_url":
        return "Web/Pages URL"
    if status == "inline_text":
        return "Inline text"
    reason = meta.get("skip_reason", "")
    if reason == "clone_failed":
        return "Repo inaccessible"
    if reason == "empty_repo":
        return "Empty repo"
    return "Graded"


def build_remark(row: dict, batch_meta: dict, user_code: str, cfg) -> str:
    remarks = []
    part_keys = cfg.part_keys
    for part_key in part_keys:
        status = row.get(f"{part_key}_status", "empty")
        url = row.get(part_key, "").strip()
        meta = batch_meta.get(f"{user_code}__{part_key}", {})
        if status == "empty":
            continue
        elif status == "pages_url":
            remarks.append(f"{part_key}: web/Pages URL submitted (not a repo)")
        elif status == "inline_text":
            remarks.append(f"{part_key}: answer pasted as text (no link)")
        elif status in ("github", "partial_path"):
            reason = meta.get("skip_reason", "")
            if reason == "clone_failed":
                remarks.append(f"{part_key}: repo inaccessible (private or deleted)")
            elif reason == "empty_repo":
                remarks.append(f"{part_key}: repo exists but empty")
            elif meta.get("skip"):
                remarks.append(f"{part_key}: {reason}")
            else:
                other_same = [p for p in part_keys if p != part_key
                              and row.get(p, "").strip().rstrip(".git") == url.rstrip(".git")]
                if other_same:
                    first = min([part_key] + other_same, key=lambda p: part_keys.index(p))
                    if part_key == first:
                        remarks.append(f"Single repo used for {1 + len(other_same)} parts")
                        break
    if not remarks:
        return "No submission" if int(row.get("Parts_Any_Answer", 0)) == 0 else "Normal submission"
    return "; ".join(dict.fromkeys(remarks))


def run(run_dir: str):
    cfg = load_config(run_dir)
    part_keys = cfg.part_keys

    with open(cfg.paths.clean_csv) as f:
        all_students = list(csv.DictReader(f))

    batch_meta = {}
    if cfg.paths.batch_state.exists():
        batch_meta = json.loads(cfg.paths.batch_state.read_text()).get("request_meta", {})

    rows = []
    for row in all_students:
        uc = row["User Code"]
        report_path = cfg.paths.reports_dir / f"{uc}.md"

        base = {"User Code": uc}
        for col in cfg.sheet.get("extra_cols", []):
            base[col] = row.get(col, "")
        base["Submission Time"] = row.get("Submission Time", "")
        base["Parts_Submitted"] = row.get("Parts_Any_Answer", "0")

        for pk in part_keys:
            base[f"{pk}_URL"] = row.get(pk, "")
            base[f"{pk}_URL_status"] = row.get(f"{pk}_status", "empty")

        if report_path.exists():
            p = parse_report(report_path.read_text(), cfg)
            for pk in part_keys:
                base[f"{pk}_score"] = p.get(f"{pk}_score", 0)
                base[f"{pk}_max"] = p.get(f"{pk}_max", cfg.part_max[pk])
                base[f"{pk}_feedback"] = p.get(f"{pk}_feedback", "")
            base["Total_Score"] = p.get("total_earned", 0)
            base["Total_Max"] = p.get("total_possible", cfg.total_marks)
            base["Percentage"] = p.get("percentage", 0.0)
            base["Remark"] = build_remark(row, batch_meta, uc, cfg)
        else:
            for pk in part_keys:
                base[f"{pk}_score"] = 0
                base[f"{pk}_max"] = cfg.part_max[pk]
                base[f"{pk}_feedback"] = "Not graded"
            base["Total_Score"] = 0
            base["Total_Max"] = cfg.total_marks
            base["Percentage"] = 0.0
            base["Remark"] = "No submission"

        # Friendly per-part status for the results sheet
        for pk in part_keys:
            base[f"{pk}_Status"] = part_status_label(row, batch_meta, uc, pk)

        rows.append(base)

    fieldnames = (
        ["User Code"] + cfg.sheet.get("extra_cols", []) + ["Submission Time", "Parts_Submitted"]
        + [c for p in part_keys for c in [f"{p}_URL", f"{p}_URL_status"]]
        + [c for p in part_keys for c in [f"{p}_score", f"{p}_max", f"{p}_feedback"]]
        + [f"{p}_Status" for p in part_keys]
        + ["Total_Score", "Total_Max", "Percentage", "Remark"]
    )
    with open(cfg.paths.master_csv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)

    print(f"Written: {cfg.paths.master_csv}\nRows: {len(rows)} students")
    graded = [r for r in rows if r["Total_Max"] > 0 and r["Remark"] != "No submission"]
    print(f"\nGraded: {len(graded)} students")
    if graded:
        pcts = [float(r["Percentage"]) for r in graded]
        print(f"Average: {sum(pcts)/len(pcts):.1f}%")
        print(f"Highest: {max(pcts):.1f}%")
        nonzero = [p for p in pcts if p > 0]
        if nonzero:
            print(f"Lowest (excl. zero): {min(nonzero):.1f}%")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Build master grading CSV")
    add_run_arg(ap)
    args = ap.parse_args()
    run(args.run)
