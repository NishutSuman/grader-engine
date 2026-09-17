"""Generate BITSoM PMAI-3 report cards from mentor grading data already in
the 4 PS sheets, upload to S3, and write the Report Card Link column back.

STATUS AS OF 2026-09-17 — READ BEFORE RUNNING ON THE FULL BATCH.
See ../README.md "Where this actually stands" section for the full story.
Summary: verified correct on 10 hand-picked students (2 rounds — the first
round had 3 real defects, described in the README, all fixed here). NOT yet
re-verified against the two edge cases below, which a full-scale scan found
in the live data:

  1. 189 of 420 phase-3-scored rows tagged "Group:" have NO "Group Feedback"
     / "Individual Feedback" labels in their raw text — the split below
     switches on the ACTUAL PRESENCE of those labels in the text (not on the
     group_tag), which should handle this correctly, but has only been
     reasoned through, not re-run and manually checked against real output.
  2. 49 rows have a real Phase 3 score but EMPTY feedback text — handled
     below by skipping the LLM entirely and writing an honest "not recorded"
     line, rather than letting the LLM invent something to fill the field.
  3. UNRESOLVED, genuinely ambiguous, not something this script tries to
     solve: some of the label-less "Group:" rows contain a single feedback
     blob describing MULTIPLE named individuals at once (e.g. "She
     handled... He delivered... She presented..."). Every member of that
     group would get the identical blob under "unified feedback" — it's
     honest (nothing invented) but not properly personalized. Flagging for
     a human decision, not silently working around it.

Before running on all ~630 rows: pick a fresh sample (include some of the
189 label-less group rows and some of the 49 empty-feedback rows on purpose)
and manually compare output against raw source, same as the README's
verification method, before trusting this at full scale.

Run from the Capstone Grader repo root:
    python3 BitSoM-PMAI-3-Capstone/scripts/generate_report_cards.py
"""
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import anthropic
from grader.storage import _load_env
_load_env()
from grader.report_pdf import generate_pdf
from grader import storage

from sheet_config import SHEETS
from sheet_io import get_values, put_values, col_letter, find_columns

client = anthropic.Anthropic()

TOOL_SOLO = {
    "name": "submit_polished_feedback",
    "description": "Submit reformatted, polished mentor feedback for a report card (unified — no group/individual split).",
    "input_schema": {
        "type": "object",
        "properties": {
            "phase2_feedback": {"type": "string"},
            "phase3_feedback": {"type": "string"},
        },
        "required": ["phase2_feedback", "phase3_feedback"],
    },
}
TOOL_GROUP = {
    "name": "submit_polished_feedback",
    "description": "Submit reformatted, polished mentor feedback for a report card (group — has explicit Group/Individual labels in the raw text).",
    "input_schema": {
        "type": "object",
        "properties": {
            "phase2_feedback": {"type": "string"},
            "phase3_feedback": {"type": "string"},
        },
        "required": ["phase2_feedback", "phase3_feedback"],
    },
}

BASE_RULES = """You are formatting existing mentor-written feedback into a clean, professional report-card section.

STRICT RULES:
- Do NOT invent, add, infer, or embellish any claim, observation, or judgment that is not already explicitly present in the raw text. Never add words like "the team" or "from the team" unless the raw text itself uses team/group language.
- Do NOT remove or soften any substantive point from the raw text either — every real point the mentor made must survive in the output, even if you need more bullet lines to fit them all in.
- You may ONLY: fix grammar/spelling/capitalization, merge fragmented sentences, remove duplicate/redundant phrasing, and organize into a clear structure.
- Never add generic filler like "great job" or "keep up the good work" unless the mentor's own words said something equivalent.
- Never output a placeholder like "<UNKNOWN>" or similar — if a section genuinely has no content, omit that section/bullet entirely rather than inventing or stubbing it.

PHASE 2 OUTPUT FORMAT (plain text, no markdown symbols like ** or #):
What Went Well
<one short bullet-style line per point, each starting with "- ">

Areas for Improvement
<one short bullet-style line per point, each starting with "- ">
"""

SYSTEM_SOLO = BASE_RULES + """
PHASE 3 OUTPUT FORMAT (the raw text has NO explicit group/individual split — do not invent one, do not mention "the team" or "group" anywhere):
What Went Well
<bullet lines>

Areas for Improvement
<bullet lines>
"""

SYSTEM_GROUP = BASE_RULES + """
PHASE 3 OUTPUT FORMAT (the raw text below already separates group-level feedback from this student's
individual feedback, under headers like "Group Feedback" / "Individual Feedback". Reformat each into
its own section; do not blend them, do not invent content for either section beyond what's in the
matching raw section):
Group Performance
<bullet lines, from the raw text's own group-level feedback only>

Individual Performance
<bullet lines, from the raw text's own individual-level feedback only>
"""

NOT_RECORDED = "No written feedback was recorded for this section."


def has_group_labels(raw_text: str) -> bool:
    return "Group Feedback" in raw_text or "Individual Feedback" in raw_text


def polish(p2_raw: str, p3_raw: str) -> dict:
    p3_raw = (p3_raw or "").strip()
    if not p3_raw:
        # don't let the LLM invent phase-3 content for a real score with no written feedback
        is_group = False
    else:
        is_group = has_group_labels(p3_raw)

    system = SYSTEM_GROUP if is_group else SYSTEM_SOLO
    tool = TOOL_GROUP if is_group else TOOL_SOLO
    user = f"RAW PHASE 2 FEEDBACK:\n{p2_raw}\n\nRAW PHASE 3 FEEDBACK:\n{p3_raw or '(none provided)'}"
    msg = client.messages.create(
        model="claude-sonnet-4-6", max_tokens=2000,
        system=system, tools=[tool],
        tool_choice={"type": "tool", "name": "submit_polished_feedback"},
        messages=[{"role": "user", "content": user}],
    )
    tu = next(x for x in msg.content if getattr(x, "type", None) == "tool_use")
    out = dict(tu.input)
    if not p3_raw:
        out["phase3_feedback"] = NOT_RECORDED
    return out


BRANDING = {
    "report_title": "BITSoM PMAI-3", "report_subtitle": "Product Management with Generative & Agentic AI — Capstone",
    "badge_text": "GRADE REPORT", "footer_note": "BITSoM x Masai — PMAI Cohort 3 Capstone",
    "author": "GradePilot", "drive_folder": "bitsom-pmai3-capstone",
    "sheet_title": "BITSoM PMAI-3 Capstone Grading",
}
PART_DEFS = [
    ("PHASE2", "Phase 2", "Project Submission", 20),
    ("PHASE3", "Phase 3", "Project Presentation", 10),
]

OUT_DIR = Path(__file__).resolve().parent / "output" / "cards"


def generate_for_sheet(ps_name: str, sheet_id: str, limit: int | None = None) -> list[dict]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    rows = get_values(sheet_id, "A1:CB2000")
    header1, header2 = rows[0], rows[1]
    cols = find_columns(header1, header2)
    if cols["p2_score_idx"] is None or cols["p3_score_idx"] is None:
        raise RuntimeError(
            f"{ps_name}: Phase 2/3 Score (computed) columns not found — "
            "run compute_and_write_sums.py first.")
    p2c, p3c = cols["p2_score_idx"], cols["p3_score_idx"]
    data = rows[3:]

    results = []
    for i, row in enumerate(data):
        def get(j):
            return row[j] if len(row) > j else ""
        code = get(2)
        if not code:
            continue
        p2_total, p3_total = get(p2c), get(p3c)
        if p2_total == "":
            continue  # not gradeable yet — mentor hasn't finished Phase 2
        p2_fb, p3_fb = get(cols["p2fb_idx"]), get(cols["p3fb_idx"])
        total = round(float(p2_total) + (float(p3_total) if p3_total != "" else 0), 2)

        polished = polish(p2_fb, p3_fb)
        row_data = {
            "User Code": code, "Name": get(1), "Email": get(0),
            "Total_Score": total, "Total_Max": 30,
            "PHASE2_score": p2_total, "PHASE2_max": 20, "PHASE2_feedback": polished["phase2_feedback"],
            "PHASE3_score": p3_total if p3_total != "" else 0, "PHASE3_max": 10,
            "PHASE3_feedback": polished["phase3_feedback"],
        }
        card_path = OUT_DIR / f"{ps_name}_{code}.pdf"
        generate_pdf(row_data, card_path, OUT_DIR, PART_DEFS, BRANDING, 30, 30)
        key = f"bitsom-pmai3-capstone/cards/{ps_name}_{code}.pdf"
        url = storage.upload(card_path, key)
        results.append({"row_idx": i, "code": code, "name": get(1), "url": url})
        print(f"{ps_name} {code} ({get(1)}) -> {url}")
        if limit and len(results) >= limit:
            break
    return results


def write_links_back(sheet_id: str, results: list[dict], cols: dict) -> None:
    """Write the Report Card Link column. Reuses the existing column if one
    was already written by a prior run (idempotent); otherwise appends a new
    one after whatever the sheet's last column currently is. NOT yet done
    for any student as of 2026-09-17."""
    idx = cols["report_link_idx"] if cols["report_link_idx"] is not None else cols["total_cols"]
    c3 = col_letter(idx)
    put_values(sheet_id, f"{c3}2:{c3}2", [["Report Card Link"]])
    for r in results:
        row_num = 4 + r["row_idx"]
        put_values(sheet_id, f"{c3}{row_num}:{c3}{row_num}", [[r["url"]]])


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--ps", choices=list(SHEETS) + ["all"], default="all")
    ap.add_argument("--limit", type=int, default=None, help="cap per sheet, for testing")
    ap.add_argument("--write-links", action="store_true", help="write the Report Card Link column back to the sheet")
    args = ap.parse_args()

    targets = SHEETS.items() if args.ps == "all" else [(args.ps, SHEETS[args.ps])]
    for name, sid in targets:
        results = generate_for_sheet(name, sid, limit=args.limit)
        if args.write_links and results:
            rows = get_values(sid, "A1:CB2")
            cols = find_columns(rows[0], rows[1])
            write_links_back(sid, results, cols)
            print(f"{name}: wrote {len(results)} report card links back to the sheet")
