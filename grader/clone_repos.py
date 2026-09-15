#!/usr/bin/env python3
"""
Stage 2 — Clone all student GitHub repos into ``<run>/cloned_repos/``.

Deduplicates by URL (a student who submits one repo for several parts is cloned
once). Skips entirely if the assignment has no github submissions. Safe to
re-run: already-cloned repos are skipped unless --force.

Usage:
  python -m grader.clone_repos --run runs/<slug>
  python -m grader.clone_repos --run runs/<slug> --force
"""

import argparse
import csv
import json
import re
import subprocess
import sys

from grader.config import add_run_arg, load_config


def normalize_url(url: str) -> str:
    url = url.strip().rstrip("/")
    if url.endswith(".git"):
        url = url[:-4]
    return url


def url_to_dirname(url: str) -> str:
    """https://github.com/owner/repo -> owner__repo"""
    m = re.match(r"https?://github\.com/([^/]+)/([^/]+)$", url)
    if m:
        return f"{m.group(1)}__{m.group(2)}"
    return re.sub(r"[^a-zA-Z0-9_.-]", "_", url)[-80:]


def collect_unique_urls(students: list, part_keys: list) -> dict:
    urls = {}
    for row in students:
        for part in part_keys:
            url = row.get(part, "").strip()
            status = row.get(f"{part}_status", "empty")
            if status in ("github", "partial_path") and url:
                norm = normalize_url(url)
                urls.setdefault(norm, url_to_dirname(norm))
    return urls


def run(run_dir: str, force: bool = False):
    cfg = load_config(run_dir)

    if "github" not in cfg.submission_types:
        print("No github submissions configured — skipping clone stage.")
        return

    with open(cfg.paths.clean_csv) as f:
        students = list(csv.DictReader(f))

    cfg.paths.clones_dir.mkdir(parents=True, exist_ok=True)
    urls = collect_unique_urls(students, cfg.part_keys)
    print(f"Unique repos to clone: {len(urls)}\n")

    log = {}
    if cfg.paths.clone_log.exists():
        log = json.loads(cfg.paths.clone_log.read_text())

    success = skipped = failed = 0
    for i, (url, dirname) in enumerate(sorted(urls.items()), 1):
        dest = cfg.paths.clones_dir / dirname
        already = dest.exists() and log.get(url, {}).get("status") == "ok"
        if not force and already:
            skipped += 1
            print(f"[{i}/{len(urls)}] SKIP  {dirname}")
            continue

        print(f"[{i}/{len(urls)}] Cloning {url}")
        try:
            result = subprocess.run(
                ["git", "clone", "--depth", "1", "--quiet", url, str(dest)],
                capture_output=True, text=True, timeout=90,
            )
            if result.returncode == 0:
                log[url] = {"status": "ok", "dir": dirname}
                success += 1
                print(f"         -> OK  ({dirname})")
            else:
                err = (result.stderr or result.stdout).strip()[:300]
                log[url] = {"status": "failed", "dir": dirname, "error": err}
                failed += 1
                print(f"         -> FAILED: {err}")
        except subprocess.TimeoutExpired:
            log[url] = {"status": "timeout", "dir": dirname, "error": "timed out after 90s"}
            failed += 1
            print("         -> TIMEOUT")
        except Exception as e:
            log[url] = {"status": "error", "dir": dirname, "error": str(e)}
            failed += 1
            print(f"         -> ERROR: {e}")

        cfg.paths.clone_log.write_text(json.dumps(log, indent=2))

    print(f"\n{'-'*50}")
    print(f"Cloned: {success}  |  Skipped: {skipped}  |  Failed: {failed}")
    print(f"Log written: {cfg.paths.clone_log}")

    if failed:
        print("\nFailed repos:")
        for url, info in log.items():
            if info.get("status") != "ok":
                print(f"  {url}\n    {info.get('error', 'unknown error')}")

    if failed and success == 0 and skipped == 0:
        sys.exit(1)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Clone student GitHub repos")
    add_run_arg(ap)
    ap.add_argument("--force", action="store_true", help="Re-clone already-cloned repos")
    args = ap.parse_args()
    run(args.run, force=args.force)
