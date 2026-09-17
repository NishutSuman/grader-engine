"""Shared read/write helpers for the 4 PS sheets. Import this from the repo
root (run scripts with `python3 BitSoM-PMAI-3-Capstone/scripts/<name>.py`
from the Capstone Grader repo root, or adjust sys.path as these scripts do)."""
from __future__ import annotations

import urllib.parse

import requests

from grader.results_sheet import _token
from sheet_config import TAB


def token_header() -> dict:
    return {"Authorization": f"Bearer {_token()}"}


def get_values(sheet_id: str, a1_range: str, unformatted: bool = True) -> list[list]:
    H = token_header()
    rng = urllib.parse.quote(f"'{TAB}'!{a1_range}")
    params = "?valueRenderOption=UNFORMATTED_VALUE" if unformatted else ""
    r = requests.get(
        f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}/values/{rng}{params}",
        headers=H, timeout=60)
    r.raise_for_status()
    return r.json().get("values", [])


def put_values(sheet_id: str, a1_range: str, values: list[list]) -> dict:
    H = token_header()
    rng = urllib.parse.quote(f"'{TAB}'!{a1_range}")
    r = requests.put(
        f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}/values/{rng}?valueInputOption=RAW",
        headers=H, timeout=90, json={"values": values})
    r.raise_for_status()
    return r.json()


def col_letter(idx: int) -> str:
    """0-based column index -> A1 letter(s)."""
    letters = ""
    idx += 1
    while idx > 0:
        idx, rem = divmod(idx - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def find_columns(header_row1: list, header_row2: list) -> dict:
    """Resolve the column indices that matter, by searching header TEXT —
    never hardcode indices, PS3's sheet is offset by 1 column vs the others.

    IMPORTANT: 'total_cols' is "wherever the sheet currently ends" — correct
    for compute_and_write_sums.py, which appends NEW columns there. It is
    NOT the position of the already-added Score columns once they exist —
    use 'p2_score_idx'/'p3_score_idx' (found by literal header text) to
    read those back, e.g. from generate_report_cards.py. Mixing these up
    silently reads empty columns past the real data — caught this exact bug
    testing generate_report_cards.py before handoff, see README."""
    def find(row, needle):
        return next(i for i, v in enumerate(row) if needle in str(v))

    def find_optional(row, needle):
        return next((i for i, v in enumerate(row) if needle in str(v)), None)

    return {
        "p2_start": 13,  # constant: fixed-width student-details+logistics block precedes it
        "p2fb_idx": find(header_row2, "Detailed Submission Feedback"),
        "p3_start": find(header_row2, "Live MVP Demo"),
        "p3fb_idx": find(header_row2, "Detailed Presentation Feedback"),
        "total_cols": len(header_row2),
        "p2_score_idx": find_optional(header_row2, "Phase 2 Score (computed)"),
        "p3_score_idx": find_optional(header_row2, "Phase 3 Score (computed)"),
        "report_link_idx": find_optional(header_row2, "Report Card Link"),
    }
