#!/usr/bin/env python3
"""
Load and validate a per-assignment grading config.

Every other module in ``grader/`` gets all assignment-specific knowledge from
here — rubrics, branding, submission-sheet layout, model choice — so nothing is
hardcoded to a single assignment.

A grading job is a directory (the "run dir"), e.g. ``runs/dbms-ga1/``, that
contains a ``config.yaml``. All input/output paths are resolved relative to it.

Usage:
    from grader.config import load_config
    cfg = load_config("runs/dbms-ga1")
    cfg.part_keys            # ["Part1_...", ...]
    cfg.rubrics["Part1_..."] # {"title", "total_marks", "breakdown", ...}
    cfg.paths.clean_csv      # runs/dbms-ga1/submissions_clean.csv
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("ERROR: PyYAML not installed. Run: pip install -r requirements.txt")


# ── Path resolver ─────────────────────────────────────────────────────────────
@dataclass
class RunPaths:
    """All file/dir paths for a single grading run, resolved from the run dir."""
    run_dir: Path
    raw_csv: Path
    clean_csv: Path
    clones_dir: Path
    clone_log: Path
    batch_state: Path
    reports_dir: Path
    prompts_dir: Path
    master_csv: Path
    summary_csv: Path
    cards_dir: Path
    problem_statement: Path


@dataclass
class Config:
    run_dir: Path
    raw: dict

    # assignment
    slug: str
    title: str
    total_marks: int
    normalize_to: int

    branding: dict
    model: dict
    sheet: dict
    submission_types: list

    parts: list  # ordered list of part dicts

    paths: RunPaths = field(init=False)

    def __post_init__(self):
        rd = self.run_dir
        raw_name = self.sheet.get("raw_file", "submissions_raw.csv")
        self.paths = RunPaths(
            run_dir=rd,
            raw_csv=rd / raw_name,
            clean_csv=rd / "submissions_clean.csv",
            clones_dir=rd / "cloned_repos",
            clone_log=rd / "clone_log.json",
            batch_state=rd / "batch_state.json",
            reports_dir=rd / "grading_reports",
            prompts_dir=rd / "prompts",
            master_csv=rd / "grading_master.csv",
            summary_csv=rd / "grading_summary.csv",
            cards_dir=rd / "report_cards",
            problem_statement=rd / "problem_statement.md",
        )

    # ── Convenience views ─────────────────────────────────────────────────────
    @property
    def part_keys(self) -> list:
        return [p["key"] for p in self.parts]

    @property
    def part_max(self) -> dict:
        return {p["key"]: p["total_marks"] for p in self.parts}

    @property
    def rubrics(self) -> dict:
        """{part_key: {title, total_marks, breakdown, required_files, problem_statement}}"""
        return {
            p["key"]: {
                "title": p["title"],
                "total_marks": p["total_marks"],
                "breakdown": p.get("breakdown", {}),
                "required_files": p.get("required_files", []),
                "problem_statement": p.get("problem_statement", ""),
            }
            for p in self.parts
        }

    def part_defs(self) -> list:
        """[(key, short_label, title_wo_label, total_marks)] for the PDF layout."""
        defs = []
        for i, p in enumerate(self.parts, 1):
            title = p["title"]
            label = f"Part {i}"
            # If the title already begins with "Part N — ", split it off cleanly.
            short = title
            for sep in (" — ", " - ", ": "):
                if sep in title:
                    left, right = title.split(sep, 1)
                    lbl = left.strip()
                    # "Part N —", "Section N —", or a short question code like "B1 —" / "D2 —"
                    if (lbl.lower().startswith(("part", "section"))
                            or (len(lbl) <= 4 and any(c.isdigit() for c in lbl))):
                        label, short = lbl, right.strip()
                    break
            defs.append((p["key"], label, short, p["total_marks"]))
        return defs


# ── Loader + validation ───────────────────────────────────────────────────────
def load_config(run_dir: str | Path) -> Config:
    run_dir = Path(run_dir)
    cfg_path = run_dir / "config.yaml"
    if not cfg_path.exists():
        # Allow pointing directly at a config file too.
        alt = Path(run_dir)
        if alt.suffix in (".yaml", ".yml") and alt.exists():
            cfg_path = alt
            run_dir = alt.parent
        else:
            sys.exit(f"ERROR: config not found at {cfg_path}")

    raw = yaml.safe_load(cfg_path.read_text()) or {}
    a = raw.get("assignment", {})
    parts = raw.get("parts", [])

    errors = []
    if not a.get("slug"):
        errors.append("assignment.slug is required")
    if not a.get("title"):
        errors.append("assignment.title is required")
    if not parts:
        errors.append("at least one entry under 'parts' is required")

    keys = [p.get("key") for p in parts]
    for i, p in enumerate(parts):
        if not p.get("key"):
            errors.append(f"parts[{i}].key is required")
        if not p.get("title"):
            errors.append(f"parts[{i}].title is required")
        if "total_marks" not in p:
            errors.append(f"parts[{i}] ({p.get('key')}): total_marks is required")
        bd = p.get("breakdown", {})
        if bd and sum(bd.values()) != p.get("total_marks"):
            errors.append(
                f"part '{p.get('key')}': breakdown sums to {sum(bd.values())} "
                f"but total_marks is {p.get('total_marks')}"
            )
    if len(keys) != len(set(keys)):
        errors.append("duplicate part keys found")

    total_marks = a.get("total_marks", sum(p.get("total_marks", 0) for p in parts))
    parts_sum = sum(p.get("total_marks", 0) for p in parts)
    if total_marks != parts_sum:
        errors.append(
            f"assignment.total_marks ({total_marks}) != sum of part marks ({parts_sum})"
        )

    sheet = raw.get("sheet", {})
    layout = sheet.get("layout", "long")
    if layout not in ("long", "wide"):
        errors.append(f"sheet.layout must be 'long' or 'wide', got '{layout}'")
    if layout == "long":
        if not sheet.get("question_id_col"):
            errors.append("sheet.question_id_col is required for layout 'long'")
        if not sheet.get("answer_col"):
            errors.append("sheet.answer_col is required for layout 'long'")
        qmap = sheet.get("question_map", {})
        for qid, pk in qmap.items():
            if pk not in keys:
                errors.append(f"sheet.question_map maps {qid} -> unknown part '{pk}'")
    else:  # wide
        for p in parts:
            if not p.get("column"):
                errors.append(
                    f"part '{p.get('key')}': 'column' is required for layout 'wide'"
                )

    if not sheet.get("student_id_col"):
        errors.append("sheet.student_id_col is required")

    if errors:
        print("Config validation failed:")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)

    return Config(
        run_dir=run_dir,
        raw=raw,
        slug=a["slug"],
        title=a["title"],
        total_marks=total_marks,
        normalize_to=a.get("normalize_to", 10),
        branding=raw.get("branding", {}),
        model=raw.get("model", {}),
        sheet=sheet,
        submission_types=raw.get("submissions", {}).get("types", ["github", "inline_text"]),
        parts=parts,
    )


def add_run_arg(parser: argparse.ArgumentParser):
    """Shared --run argument for every CLI module."""
    parser.add_argument(
        "--run", required=True,
        help="Path to the run directory (contains config.yaml), e.g. runs/dbms-ga1",
    )


if __name__ == "__main__":
    # `python -m grader.config --run <dir>` validates and prints a summary.
    ap = argparse.ArgumentParser(description="Validate a grading config")
    add_run_arg(ap)
    args = ap.parse_args()
    cfg = load_config(args.run)
    print(f"OK  {cfg.slug} — {cfg.title}")
    print(f"    total: {cfg.total_marks} marks, normalise to /{cfg.normalize_to}")
    print(f"    submissions: {', '.join(cfg.submission_types)}")
    print(f"    sheet layout: {cfg.sheet.get('layout', 'long')}")
    print(f"    parts ({len(cfg.parts)}):")
    for k, label, short, m in cfg.part_defs():
        print(f"      - {k}: {label} — {short} ({m})")
