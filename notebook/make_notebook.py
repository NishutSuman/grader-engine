#!/usr/bin/env python3
"""
Stage 7 (builder) — Generate a Colab notebook that renders PDF report cards,
uploads them to a Google Drive folder, and creates a Google Sheet of public
links. Branding, folder name, and sheet title come from the run's config; the
PDF engine is embedded verbatim from grader/report_pdf.py (single source of
truth).

Usage:
  python notebook/make_notebook.py --run runs/<slug>
  # -> writes <run>/Grade_Reports.ipynb  (open in Google Colab)
"""

import argparse
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from grader.config import add_run_arg, load_config  # noqa: E402


def _engine_source() -> str:
    src = (REPO / "grader" / "report_pdf.py").read_text()
    m = re.search(r"# <<ENGINE_START>>(.*?)# <<ENGINE_END>>", src, re.DOTALL)
    if not m:
        sys.exit("ERROR: engine markers not found in grader/report_pdf.py")
    return m.group(1).strip("\n")


def code_cell(src):
    return {"cell_type": "code", "execution_count": None, "metadata": {}, "outputs": [], "source": src.strip("\n")}


def md_cell(src):
    return {"cell_type": "markdown", "metadata": {}, "source": src.strip("\n")}


def build(run_dir: str):
    cfg = load_config(run_dir)
    b = cfg.branding
    drive_folder = b.get("drive_folder", f"{cfg.slug} Reports")
    sheet_title = b.get("sheet_title", f"{cfg.title} — Links")
    part_defs = cfg.part_defs()
    extra_cols = cfg.sheet.get("extra_cols", [])
    id_label = cfg.sheet.get("id_label", "User Code")

    C0 = md_cell(f"""# {cfg.title} · PDF Report Generator

Generates one PDF report card per student from `grading_master.csv`, uploads them
to a Google Drive folder **{drive_folder}**, and creates a Google Sheet listing
every student's public PDF link.

### Before you run
Upload two files when prompted:
1. **`grading_master.csv`** — from your run directory.
2. **`grading_reports.zip`** — zip of your run's `grading_reports/` folder:
   ```bash
   cd <your run dir> && zip -r grading_reports.zip grading_reports/
   ```

### Run order
| Cell | What it does |
|------|--------------|
| 1 | Install `reportlab` |
| 2 | Google auth + imports |
| 3 | Upload the two source files |
| 4 | Load PDF engine (embedded from this project) |
| 5 | Generate all PDFs |
| 6 | Upload PDFs → Drive folder **{drive_folder}** |
| 7 | Create Google Sheet of public links |""")

    C1 = code_cell("!pip install reportlab --quiet\nprint('reportlab ready')")

    C2 = code_cell("""from google.colab import auth
auth.authenticate_user()

import csv, re, os, zipfile
from pathlib import Path

from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.colors import HexColor
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable, KeepTogether,
)
print('Authenticated. Libraries loaded.')""")

    C3 = code_cell("""from google.colab import files as colab_files

print('Step 1 of 2 - Upload grading_master.csv')
up1 = colab_files.upload()
for name, data in up1.items():
    Path('/content/grading_master.csv').write_bytes(data)
    print(f'  Saved grading_master.csv ({len(data):,} bytes)')

print('\\nStep 2 of 2 - Upload grading_reports.zip')
up2 = colab_files.upload()
REPORTS_DIR = Path('/content/grading_reports')
REPORTS_DIR.mkdir(exist_ok=True)
for name, data in up2.items():
    zp = Path(f'/content/{name}'); zp.write_bytes(data)
    with zipfile.ZipFile(zp, 'r') as z:
        z.extractall('/content/')
    print(f'  Extracted. .md files: {len(list(REPORTS_DIR.glob("*.md")))}')
    break

with open('/content/grading_master.csv') as f:
    _rows = list(csv.DictReader(f))
print(f'\\nCSV loaded: {len(_rows)} students')""")

    # Cell 4: embedded engine + config-derived literals
    literals = (
        f"PART_DEFS = {json.dumps(part_defs)}\n"
        f"BRANDING = {json.dumps(b)}\n"
        f"TOTAL_MAX = {cfg.total_marks}\n"
        f"NORMALIZE_TO = {cfg.normalize_to}\n"
    )
    C4 = code_cell(_engine_source() + "\n\n" + literals + "\nprint('PDF engine loaded.')")

    C5 = code_cell("""PDFS_DIR = Path('/content/pdfs'); PDFS_DIR.mkdir(exist_ok=True)
REPORTS_DIR = Path('/content/grading_reports')

with open('/content/grading_master.csv') as f:
    students = list(csv.DictReader(f))

print(f'Generating PDFs for {len(students)} students...\\n')
for i, row in enumerate(students, 1):
    generate_pdf(row, PDFS_DIR / f"{row['User Code']}.pdf", REPORTS_DIR,
                 PART_DEFS, BRANDING, TOTAL_MAX, NORMALIZE_TO)
    if i % 10 == 0 or i == len(students):
        print(f'  {i} / {len(students)} complete')
print(f"\\nAll {len(list(PDFS_DIR.glob('*.pdf')))} PDFs saved.")""")

    C6 = code_cell(f"""drive_service = build('drive', 'v3')
DRIVE_FOLDER = {json.dumps(drive_folder)}

res = drive_service.files().list(
    q=(f"name='{{DRIVE_FOLDER}}' and mimeType='application/vnd.google-apps.folder' and trashed=false"),
    fields='files(id, name)').execute()
if res['files']:
    folder_id = res['files'][0]['id']; print(f'Using existing folder: {{DRIVE_FOLDER}}')
else:
    f = drive_service.files().create(
        body={{'name': DRIVE_FOLDER, 'mimeType': 'application/vnd.google-apps.folder'}},
        fields='id').execute()
    folder_id = f['id']; print(f'Created Drive folder: {{DRIVE_FOLDER}}')

pdf_links = {{}}
pdf_files = sorted(Path('/content/pdfs').glob('*.pdf'))
print(f'Uploading {{len(pdf_files)}} PDFs...\\n')
for i, pdf_path in enumerate(pdf_files, 1):
    user_code = pdf_path.stem
    media = MediaFileUpload(str(pdf_path), mimetype='application/pdf', resumable=False)
    existing = drive_service.files().list(
        q=(f"name='{{user_code}}.pdf' and '{{folder_id}}' in parents and trashed=false"),
        fields='files(id)').execute()
    if existing['files']:
        file_id = existing['files'][0]['id']
        drive_service.files().update(fileId=file_id, media_body=media).execute()
    else:
        new_f = drive_service.files().create(
            body={{'name': f'{{user_code}}.pdf', 'parents': [folder_id]}},
            media_body=media, fields='id').execute()
        file_id = new_f['id']
    try:
        drive_service.permissions().create(
            fileId=file_id, body={{'type': 'anyone', 'role': 'reader'}}).execute()
    except Exception:
        pass
    info = drive_service.files().get(fileId=file_id, fields='webViewLink').execute()
    pdf_links[user_code] = info.get('webViewLink', '')
    if i % 10 == 0 or i == len(pdf_files):
        print(f'  {{i}} / {{len(pdf_files)}} uploaded')
print(f'\\nAll {{len(pdf_links)}} PDFs uploaded.')
print(f'Folder: https://drive.google.com/drive/folders/{{folder_id}}')""")

    C7 = code_cell(f"""sheets_service = build('sheets', 'v4')
EXTRA_COLS = {json.dumps(extra_cols)}
ID_LABEL = {json.dumps(id_label)}
SHEET_TITLE = {json.dumps(sheet_title)}
# PART_DEFS (from the engine cell): [key, label, title, max] per part

with open('/content/grading_master.csv') as f:
    students_dict = {{r['User Code']: r for r in csv.DictReader(f)}}

part_headers   = [f'{{label}} — {{title}}' for (k, label, title, m) in PART_DEFS]
status_headers = [f'P{{i}} Status' for i in range(1, len(PART_DEFS) + 1)]
header = ([ID_LABEL] + EXTRA_COLS + ['Total Score', f'Out of {{NORMALIZE_TO}}']
          + part_headers + status_headers + ['Grade Report (PDF)'])

data_rows = []
for user_code in sorted(pdf_links.keys()):
    row = students_dict.get(user_code, {{}})
    total = float(row.get('Total_Score', 0))
    total_max = float(row.get('Total_Max', {cfg.total_marks}) or {cfg.total_marks})
    norm = round(total / total_max * NORMALIZE_TO, 1) if total_max else 0
    part_scores   = [row.get(f'{{k}}_score', '')  for (k, label, title, m) in PART_DEFS]
    part_statuses = [row.get(f'{{k}}_Status', '') for (k, label, title, m) in PART_DEFS]
    data_rows.append(
        [user_code] + [row.get(c, '') for c in EXTRA_COLS]
        + [total, norm] + part_scores + part_statuses + [pdf_links[user_code]])

ss = sheets_service.spreadsheets().create(body={{'properties': {{'title': SHEET_TITLE}}}}).execute()
ss_id = ss['spreadsheetId']
sheets_service.spreadsheets().values().update(
    spreadsheetId=ss_id, range='Sheet1!A1', valueInputOption='RAW',
    body={{'values': [header] + data_rows}}).execute()

NAVY_RGB = {{'red': 0.106, 'green': 0.165, 'blue': 0.290}}
WHITE_RGB = {{'red': 1.0, 'green': 1.0, 'blue': 1.0}}
sheets_service.spreadsheets().batchUpdate(spreadsheetId=ss_id, body={{'requests': [
    {{'repeatCell': {{'range': {{'sheetId': 0, 'startRowIndex': 0, 'endRowIndex': 1}},
        'cell': {{'userEnteredFormat': {{'textFormat': {{'bold': True, 'foregroundColor': WHITE_RGB}},
            'backgroundColor': NAVY_RGB}}}},
        'fields': 'userEnteredFormat.textFormat.bold,userEnteredFormat.textFormat.foregroundColor,userEnteredFormat.backgroundColor'}}}},
    {{'updateSheetProperties': {{'properties': {{'sheetId': 0, 'gridProperties': {{'frozenRowCount': 1}}}},
        'fields': 'gridProperties.frozenRowCount'}}}},
    {{'autoResizeDimensions': {{'dimensions': {{'sheetId': 0, 'dimension': 'COLUMNS',
        'startIndex': 0, 'endIndex': len(header)}}}}}},
]}}).execute()
print('Google Sheet created:')
print(f'  https://docs.google.com/spreadsheets/d/{{ss_id}}')
print(f'\\n{{len(data_rows)}} students | {{len(header)}} columns')""")

    notebook = {
        "nbformat": 4, "nbformat_minor": 0,
        "metadata": {"colab": {"provenance": [], "toc_visible": True},
                     "kernelspec": {"display_name": "Python 3", "name": "python3"},
                     "language_info": {"name": "python"}},
        "cells": [C0, C1, C2, C3, C4, C5, C6, C7],
    }
    out = cfg.run_dir / "Grade_Reports.ipynb"
    out.write_text(json.dumps(notebook, indent=1, ensure_ascii=False))
    print(f"Written: {out}\nCells: {len(notebook['cells'])}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Build the Colab distribution notebook")
    add_run_arg(ap)
    args = ap.parse_args()
    build(args.run)
