"""Add/refresh the 2 computed score columns on all 4 PS sheets:
'Phase 2 Score (computed)' and 'Phase 3 Score (computed)'.

No total column exists in the sheets natively — mentors only fill in the
per-sub-criterion cells, so these are summed here. Idempotent: safe to
re-run any time (e.g. after mentors add more grades) — it recomputes fresh
each time and overwrites the same 2 columns in place, appended right after
whatever the sheet's last existing column currently is.

Already run once against live data as of 2026-09-17 — re-running is only
needed if mentors have added/changed grades since.

Run from the Capstone Grader repo root:
    python3 BitSoM-PMAI-3-Capstone/scripts/compute_and_write_sums.py
"""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from sheet_config import SHEETS
from sheet_io import get_values, put_values, col_letter, find_columns


def compute_for_sheet(sheet_id: str) -> dict:
    rows = get_values(sheet_id, "A1:CB2000")
    header1, header2 = rows[0], rows[1]
    cols = find_columns(header1, header2)
    data = rows[3:]

    p2_out, p3_out = [], []
    for row in data:
        def get(i):
            return row[i] if len(row) > i else ""
        code = get(2)
        if not code:
            p2_out.append([""]); p3_out.append([""])
            continue
        p2_cells = [get(i) for i in range(cols["p2_start"], cols["p2fb_idx"])]
        p3_cells = [get(i) for i in range(cols["p3_start"], cols["p3fb_idx"])]
        p2_has_any = any(isinstance(c, (int, float)) for c in p2_cells)
        p3_has_any = any(isinstance(c, (int, float)) for c in p3_cells)
        p2_out.append([round(sum(c for c in p2_cells if isinstance(c, (int, float))), 2) if p2_has_any else ""])
        p3_out.append([round(sum(c for c in p3_cells if isinstance(c, (int, float))), 2) if p3_has_any else ""])

    return {"cols": cols, "n_rows": len(data), "p2_out": p2_out, "p3_out": p3_out}


def main():
    for name, sheet_id in SHEETS.items():
        result = compute_for_sheet(sheet_id)
        c1 = col_letter(result["cols"]["total_cols"])
        c2 = col_letter(result["cols"]["total_cols"] + 1)
        n = result["n_rows"]

        put_values(sheet_id, f"{c1}2:{c2}2",
                   [["Phase 2 Score (computed)", "Phase 3 Score (computed)"]])
        values = [[result["p2_out"][i][0], result["p3_out"][i][0]] for i in range(n)]
        put_values(sheet_id, f"{c1}4:{c2}{3 + n}", values)
        print(f"{name}: wrote headers at {c1}2:{c2}2, data at {c1}4:{c2}{3 + n}")


if __name__ == "__main__":
    main()
