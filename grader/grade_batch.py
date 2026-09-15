#!/usr/bin/env python3
"""
Stage 3 — Build one grading prompt per (student, part) and submit them as a
single Anthropic Message Batch. Non-gradeable cases (pages URLs, empty or
inaccessible repos) are pre-resolved to 0 without an AI call.

Saves batch id + per-request metadata to ``<run>/batch_state.json``.

Usage:
  python -m grader.grade_batch --run runs/<slug>
  python -m grader.grade_batch --run runs/<slug> --dry-run   # write prompts, no API call
  python -m grader.grade_batch --run runs/<slug> --limit 5
  python -m grader.grade_batch --run runs/<slug> --student A,B,C
"""

import argparse
import csv
import json
import os
import re
import sys
from pathlib import Path

from grader.config import add_run_arg, load_config

# ── File reading ──────────────────────────────────────────────────────────────
READABLE_EXTENSIONS = {".sql", ".md", ".txt", ".py", ".js", ".ts", ".java",
                       ".c", ".cpp", ".cs", ".go", ".rb", ".rs", ".html",
                       ".css", ".json", ".yaml", ".yml", ".csv", ".ipynb", ".r"}
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".github", "data",
             ".venv", "venv", "dist", "build", ".next", "target"}
MAX_FILE_CHARS = 10000
MAX_TOTAL_CHARS = 120000


def read_repo_files(clone_dir: Path, max_file_chars: int = MAX_FILE_CHARS,
                    max_total_chars: int = MAX_TOTAL_CHARS) -> dict:
    """Read relevant text files from a cloned repo. Returns {rel_path: content}.

    Per-file and total caps default to the module constants but can be raised via
    ``model.max_file_chars`` / ``model.max_total_chars`` in config.yaml — needed
    for prose submissions (e.g. a single long Markdown portfolio) that a 10k cap
    would truncate mid-answer.
    """
    files = {}
    total = 0
    for root, dirs, filenames in os.walk(clone_dir):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        rel_root = Path(root).relative_to(clone_dir)
        for fname in sorted(filenames):
            if Path(fname).suffix.lower() not in READABLE_EXTENSIONS:
                continue
            fpath = Path(root) / fname
            rel = str(rel_root / fname) if str(rel_root) != "." else fname
            try:
                raw = fpath.read_text(errors="replace")
            except Exception:
                continue
            if not raw.strip():
                continue
            content = raw[:max_file_chars]
            if total + len(content) > max_total_chars:
                remaining = max_total_chars - total
                if remaining > 200:
                    files[rel] = content[:remaining] + "\n[...truncated]"
                    total = max_total_chars
                break
            files[rel] = content
            total += len(content)
        if total >= max_total_chars:
            break
    return files


def normalize_url(url: str) -> str:
    url = url.strip().rstrip("/")
    return url[:-4] if url.endswith(".git") else url


# ── Prompt builder (domain-neutral) ───────────────────────────────────────────
def build_prompt(cfg, user_code: str, part_key: str, repo_url: str, file_contents: dict) -> str:
    rubric = cfg.rubrics[part_key]
    total = rubric["total_marks"]
    breakdown_str = "\n".join(f"- {k}: {v} marks" for k, v in rubric["breakdown"].items())
    required = rubric.get("required_files", [])
    required_str = ", ".join(required) if required else "(none specified)"
    problem = rubric.get("problem_statement", "").strip()

    if file_contents:
        files_section = "\n\n".join(
            f"### {fname}\n```\n{content}\n```" for fname, content in file_contents.items()
        )
    else:
        files_section = "(No files / content found)"

    problem_block = f"\n## Task / Problem Statement\n{problem}\n" if problem else ""
    required_block = f"\n## Expected Files\n{required_str}\n" if required else ""

    return f"""You are an expert, impartial grader for the assignment "{cfg.title}".
Grade this submission strictly and fairly against the rubric below. Ground every
comment in the actual submitted content — do not reward generic answers.

## Part: {rubric['title']} (Total: {total} marks)

## Marks Breakdown
{breakdown_str}
{problem_block}{required_block}
## Student: {user_code}
## Submission source: {repo_url}

## Submitted Content
{files_section}

---

For each component in the marks breakdown:
1. Assign a numeric score (e.g. 3/5).
2. Write 1-3 sentences of specific feedback citing actual content (or noting what is missing).

Then write an overall feedback paragraph (3-5 sentences): strengths, weaknesses, what to improve.

Respond in this EXACT JSON format and nothing else:
{{
  "breakdown": {{
    "<component name exactly as listed>": {{
      "score": <integer>,
      "max": <integer>,
      "comment": "<feedback>"
    }}
  }},
  "overall_feedback": "<paragraph>",
  "total_score": <integer>,
  "total_max": {total}
}}

Be strict but fair. Award 0 for completely missing components. Do not inflate scores."""


# ── Main ──────────────────────────────────────────────────────────────────────
def run(run_dir: str, limit=None, student=None, dry_run=False):
    cfg = load_config(run_dir)

    with open(cfg.paths.clean_csv) as f:
        students = list(csv.DictReader(f))

    clone_log = {}
    if cfg.paths.clone_log.exists():
        clone_log = json.loads(cfg.paths.clone_log.read_text())

    if student:
        targets = {c.strip() for c in student.split(",")}
        students = [s for s in students if s["User Code"] in targets]
    else:
        students = [s for s in students if int(s.get("Parts_Any_Answer", 0)) > 0]
    if limit:
        students = students[:limit]

    print(f"Building batch requests for {len(students)} students...\n")

    model = cfg.model.get("grader_model", "claude-haiku-4-5")
    max_tokens = cfg.model.get("max_tokens", 1500)
    max_file_chars = cfg.model.get("max_file_chars", MAX_FILE_CHARS)
    max_total_chars = cfg.model.get("max_total_chars", MAX_TOTAL_CHARS)

    batch_requests = []
    request_meta = {}
    student_rows = {s["User Code"]: dict(s) for s in students}

    for row in students:
        user_code = row["User Code"]
        for part_key in cfg.part_keys:
            url = row.get(part_key, "").strip()
            status = row.get(f"{part_key}_status", "empty")
            total = cfg.part_max[part_key]
            custom_id = f"{user_code}__{part_key}"

            if not url or status == "empty":
                continue

            if status == "pages_url":
                request_meta[custom_id] = {
                    "user_code": user_code, "part_key": part_key, "repo_url": url,
                    "files_found": [], "skip": True, "skip_reason": "pages_url",
                    "total_score": 0, "total_max": total,
                    "overall_feedback": (
                        f"Submission was a web/GitHub Pages URL ({url}), not a source "
                        "repository. Cannot access source files. Please submit the correct link."),
                }
                continue

            if status == "inline_text":
                inline_files = {"inline_submission.txt": url}
                prompt = build_prompt(cfg, user_code, part_key, "(inline text submission)", inline_files)
                batch_requests.append({
                    "custom_id": custom_id,
                    "params": {"model": model, "max_tokens": max_tokens,
                               "messages": [{"role": "user", "content": prompt}]},
                })
                request_meta[custom_id] = {
                    "user_code": user_code, "part_key": part_key, "repo_url": "(inline text)",
                    "files_found": ["inline_submission.txt"],
                    "note": "Graded on sheet-pasted text (may be truncated)",
                }
                continue

            # github / partial_path
            norm = normalize_url(url)
            clone_info = clone_log.get(norm, {})
            if clone_info.get("status") != "ok":
                err = clone_info.get("error", "repo not cloned (run clone_repos first)")
                request_meta[custom_id] = {
                    "user_code": user_code, "part_key": part_key, "repo_url": url,
                    "files_found": [], "skip": True, "skip_reason": "clone_failed",
                    "total_score": 0, "total_max": total,
                    "overall_feedback": f"Repository could not be accessed: {err}",
                }
                continue

            file_contents = read_repo_files(
                cfg.paths.clones_dir / clone_info["dir"],
                max_file_chars=max_file_chars, max_total_chars=max_total_chars)
            if not file_contents:
                request_meta[custom_id] = {
                    "user_code": user_code, "part_key": part_key, "repo_url": url,
                    "files_found": [], "skip": True, "skip_reason": "empty_repo",
                    "total_score": 0, "total_max": total,
                    "overall_feedback": "Repository was empty or contained no readable files.",
                }
                continue

            prompt = build_prompt(cfg, user_code, part_key, url, file_contents)
            batch_requests.append({
                "custom_id": custom_id,
                "params": {"model": model, "max_tokens": max_tokens,
                           "messages": [{"role": "user", "content": prompt}]},
            })
            request_meta[custom_id] = {
                "user_code": user_code, "part_key": part_key, "repo_url": url,
                "files_found": list(file_contents.keys()),
            }

    skipped = sum(1 for m in request_meta.values() if m.get("skip"))
    print(f"AI grading requests: {len(batch_requests)}")
    print(f"Pre-resolved (no AI needed): {skipped}")
    print(f"Total entries tracked: {len(request_meta)}")

    # ── Dry run: write prompts to disk, don't call the API ──────────────────────
    if dry_run:
        cfg.paths.prompts_dir.mkdir(parents=True, exist_ok=True)
        for req in batch_requests:
            (cfg.paths.prompts_dir / f"{req['custom_id']}.txt").write_text(
                req["params"]["messages"][0]["content"])
        print(f"\n[dry-run] Wrote {len(batch_requests)} prompts to {cfg.paths.prompts_dir}")
        print("[dry-run] No API call made, no batch state saved.")
        return

    if not batch_requests:
        print("\nNo requests to submit. Exiting.")
        return

    # ── Submit ──────────────────────────────────────────────────────────────────
    _require_api_key()
    import anthropic
    print(f"\nSubmitting batch of {len(batch_requests)} requests to {model}...")
    client = anthropic.Anthropic()
    batch = client.messages.batches.create(requests=batch_requests)
    print(f"Batch submitted!  id={batch.id}  status={batch.processing_status}")

    cfg.paths.batch_state.write_text(json.dumps({
        "batch_id": batch.id,
        "processing_status": batch.processing_status,
        "total_ai_requests": len(batch_requests),
        "model": model,
        "request_meta": request_meta,
        "student_rows": student_rows,
    }, indent=2))
    print(f"\nState saved: {cfg.paths.batch_state}")
    print(f"Next: python -m grader.collect_results --run {run_dir}")


def _require_api_key():
    _env = Path(".env")
    if _env.exists():
        for line in _env.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())
    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("ERROR: ANTHROPIC_API_KEY not set. Add it to .env")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Submit grading batch")
    add_run_arg(ap)
    ap.add_argument("--limit", type=int, help="Process only first N students")
    ap.add_argument("--student", type=str, help="Comma-separated User Codes")
    ap.add_argument("--dry-run", action="store_true", help="Write prompts to disk, no API call")
    args = ap.parse_args()
    run(args.run, limit=args.limit, student=args.student, dry_run=args.dry_run)
