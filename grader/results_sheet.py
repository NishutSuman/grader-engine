"""Push final grades back to the Outcomes master sheet as a per-eval tab, so the
Ops team sees them in the same shared workbook (no Colab, no separate export).

The Drive service account already has EDITOR access to the master sheet; this
uses the read-WRITE Sheets scope to create/refresh a tab named '<slug>-grades'
with the full master-CSV columns. Idempotent — re-pushing clears + rewrites.
"""
from __future__ import annotations

import urllib.parse

import requests

from grader.download import _sa_key_path
from grader.pending_sheet import SHEET_ID

_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


def _token() -> str:
    from google.auth.transport.requests import Request as GRequest
    from google.oauth2 import service_account
    creds = service_account.Credentials.from_service_account_file(
        str(_sa_key_path({})), scopes=_SCOPES)
    creds.refresh(GRequest())
    return creds.token


def _cols(parts) -> list[str]:
    return (["User Code", "Name", "Email", "Total_Score", "Total_Max", "Scaled", "Overall", "Remarks"]
            + [f"{p.key}_{x}" for p in parts for x in ("score", "max", "feedback")]
            + ["Report_URL"])


def push_grades(slug: str, rows: list[dict], parts, sheet_id: str = SHEET_ID) -> dict:
    tab = f"{slug}-grades"
    cols = _cols(parts)
    values = [cols] + [["" if r.get(c) is None else r.get(c) for c in cols] for r in rows]
    H = {"Authorization": f"Bearer {_token()}"}
    base = f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}"

    meta = requests.get(base + "?fields=sheets.properties(sheetId,title)",
                        headers=H, timeout=30)
    meta.raise_for_status()
    titles = {s["properties"]["title"]: s["properties"]["sheetId"]
              for s in meta.json().get("sheets", [])}

    if tab not in titles:
        r = requests.post(base + ":batchUpdate", headers=H, timeout=30,
                          json={"requests": [{"addSheet": {"properties": {"title": tab}}}]})
        r.raise_for_status()
        gid = r.json()["replies"][0]["addSheet"]["properties"]["sheetId"]
    else:
        gid = titles[tab]
        requests.post(base + f"/values/{urllib.parse.quote(chr(39) + tab + chr(39))}:clear",
                      headers=H, timeout=30).raise_for_status()

    rng = urllib.parse.quote(f"'{tab}'!A1")
    r = requests.put(base + f"/values/{rng}?valueInputOption=RAW", headers=H, timeout=90,
                     json={"values": values})
    r.raise_for_status()
    return {"tab": tab, "rows": len(rows),
            "url": f"https://docs.google.com/spreadsheets/d/{sheet_id}/edit#gid={gid}"}
