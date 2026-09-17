"""Batch statistics: overall / per-sheet / per-mentor averages and deltas.
Read-only, safe to run any time. Requires the computed score columns from
compute_and_write_sums.py to already be present on the sheets.

Run from the Capstone Grader repo root:
    python3 BitSoM-PMAI-3-Capstone/scripts/analyze_scores.py
"""
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from sheet_config import SHEETS
from sheet_io import get_values, col_letter, find_columns


def main():
    all_p2, all_p3, all_combined = [], [], []
    per_sheet = {}
    per_mentor = defaultdict(lambda: {"p2": [], "p3": [], "combined": [], "sheets": set()})

    for name, sheet_id in SHEETS.items():
        rows = get_values(sheet_id, "A1:CB2000")
        header1, header2 = rows[0], rows[1]
        cols = find_columns(header1, header2)
        c1, c2 = cols["total_cols"], cols["total_cols"] + 1
        data = rows[3:]

        p2s, p3s, combined = [], [], []
        for row in data:
            def get(i):
                return row[i] if len(row) > i else ""
            grader = str(get(12)).strip()
            p2v, p3v = get(c1), get(c2)
            if isinstance(p2v, (int, float)):
                p2s.append(p2v); all_p2.append(p2v)
                if grader:
                    per_mentor[grader]["p2"].append(p2v)
                    per_mentor[grader]["sheets"].add(name)
            if isinstance(p3v, (int, float)):
                p3s.append(p3v); all_p3.append(p3v)
                if grader:
                    per_mentor[grader]["p3"].append(p3v)
            if isinstance(p2v, (int, float)) and isinstance(p3v, (int, float)):
                c = p2v + p3v
                combined.append(c); all_combined.append(c)
                if grader:
                    per_mentor[grader]["combined"].append(c)

        per_sheet[name] = {
            "n_p2": len(p2s), "mean_p2": round(st.mean(p2s), 2) if p2s else None,
            "n_p3": len(p3s), "mean_p3": round(st.mean(p3s), 2) if p3s else None,
            "n_both": len(combined), "mean_combined": round(st.mean(combined), 2) if combined else None,
            "median_combined": round(st.median(combined), 2) if combined else None,
            "std_combined": round(st.pstdev(combined), 2) if len(combined) > 1 else None,
        }

    print("=== OVERALL (all 4 sheets combined) ===")
    print(f"Phase 2: n={len(all_p2)} mean={round(st.mean(all_p2), 2)}/20 median={round(st.median(all_p2), 2)} stdev={round(st.pstdev(all_p2), 2)}")
    print(f"Phase 3: n={len(all_p3)} mean={round(st.mean(all_p3), 2)}/10 median={round(st.median(all_p3), 2)} stdev={round(st.pstdev(all_p3), 2)}")
    print(f"Combined: n={len(all_combined)} mean={round(st.mean(all_combined), 2)}/30 median={round(st.median(all_combined), 2)} stdev={round(st.pstdev(all_combined), 2)}")
    print()
    print("=== PER SHEET ===")
    for name, d in per_sheet.items():
        print(f"{name}: P2 n={d['n_p2']} mean={d['mean_p2']}/20 | P3 n={d['n_p3']} mean={d['mean_p3']}/10 | "
              f"Combined n={d['n_both']} mean={d['mean_combined']}/30 median={d['median_combined']} std={d['std_combined']}")
    print()
    print("=== PER MENTOR (Grader / Grader IM column) ===")
    for grader, d in sorted(per_mentor.items(), key=lambda x: -len(x[1]["combined"])):
        p2m = round(st.mean(d["p2"]), 2) if d["p2"] else None
        p3m = round(st.mean(d["p3"]), 2) if d["p3"] else None
        cm = round(st.mean(d["combined"]), 2) if d["combined"] else None
        print(f"{grader!r} (sheets: {sorted(d['sheets'])}): P2 n={len(d['p2'])} mean={p2m} | "
              f"P3 n={len(d['p3'])} mean={p3m} | Combined n={len(d['combined'])} mean={cm}/30")


if __name__ == "__main__":
    main()
