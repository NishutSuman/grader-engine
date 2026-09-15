#!/usr/bin/env python3
"""
Stage 6 — Render one PDF report card per student from the master CSV.

The layout is domain-agnostic; branding (banner title/subtitle, badge, footer,
author) and the part definitions come from the run's config. This same module is
imported by the Colab notebook builder so the PDFs are identical locally and in
the cloud.

Usage:
  python -m grader.report_pdf --run runs/<slug>
  python -m grader.report_pdf --run runs/<slug> --student iitmcs_24093225
"""

import argparse
import csv
import re
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.colors import HexColor
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable, KeepTogether,
)

from grader.config import add_run_arg, load_config

# <<ENGINE_START>>  (everything down to <<ENGINE_END>> is embedded into the Colab
# notebook by notebook/make_notebook.py — keep it self-contained: rely only on
# re, Path, and the reportlab names imported at the top of this file.)

# ── Geometry ──────────────────────────────────────────────────────────────────
PAGE_W, PAGE_H = A4
LM = RM = 18 * mm
TM = 16 * mm
BM = 18 * mm
CW = PAGE_W - LM - RM

# ── Palette ───────────────────────────────────────────────────────────────────
NAVY = HexColor('#22314F')       # banner + header bars + accent numbers
ORANGE = HexColor('#E0812F')     # GRADE REPORT badge
WHITE = HexColor('#FFFFFF')
DIVIDER = HexColor('#E2E8F0')
ROW_ALT = HexColor('#F4F6FA')
TRACK = HexColor('#E6EAF2')      # empty part of a progress bar
BODY = HexColor('#3A4658')
DARK = HexColor('#1B2436')
MUTED = HexColor('#8A94A6')
BOX_BG = HexColor('#FFFFFF')
GREEN = HexColor('#3E9C50'); AMBER = HexColor('#E0A030'); RED = HexColor('#CF4C54')


def _bar_color(pct):
    return GREEN if pct >= 70 else AMBER if pct >= 50 else RED


def _ps(name, **kw):
    return ParagraphStyle(name, **kw)


S = {
    'banner_title': _ps('bt', fontName='Helvetica-Bold', fontSize=17, textColor=WHITE, leading=20),
    'banner_sub':   _ps('bs', fontName='Helvetica', fontSize=8.5, textColor=HexColor('#C7D0E0'), leading=12),
    'badge':        _ps('bd', fontName='Helvetica-Bold', fontSize=10, textColor=WHITE, leading=13, alignment=TA_CENTER),
    'student_lbl':  _ps('slb', fontName='Helvetica', fontSize=7.5, textColor=MUTED, leading=10, alignment=TA_CENTER),
    'student_code': _ps('scd', fontName='Helvetica-Bold', fontSize=19, textColor=DARK, leading=23, alignment=TA_CENTER),
    'box_label':    _ps('bl', fontName='Helvetica', fontSize=7.5, textColor=MUTED, leading=10, alignment=TA_CENTER),
    'box_num':      _ps('bn', fontName='Helvetica-Bold', fontSize=29, textColor=NAVY, leading=32, alignment=TA_CENTER),
    'box_denom':    _ps('bdn', fontName='Helvetica', fontSize=9, textColor=MUTED, leading=12, alignment=TA_CENTER),
    'bar_header':   _ps('bh', fontName='Helvetica-Bold', fontSize=9.5, textColor=WHITE, leading=12, alignment=TA_CENTER),
    'sec_name':     _ps('sn', fontName='Helvetica', fontSize=9, textColor=DARK, leading=11.5),
    'sec_score':    _ps('ss', fontName='Helvetica-Bold', fontSize=9.5, textColor=NAVY, leading=12, alignment=TA_RIGHT),
    'sec_pct':      _ps('sp', fontName='Helvetica', fontSize=8, textColor=MUTED, leading=10, alignment=TA_RIGHT),
    'part_title':   _ps('pt', fontName='Helvetica-Bold', fontSize=9.5, textColor=DARK, leading=13),
    'part_score':   _ps('psc', fontName='Helvetica-Bold', fontSize=9.5, textColor=NAVY, leading=13, alignment=TA_RIGHT),
    'repo_url':     _ps('ru', fontName='Helvetica', fontSize=7, textColor=MUTED, leading=10),
    'feedback':     _ps('fb', fontName='Helvetica', fontSize=8.5, textColor=BODY, leading=12.5),
}


def _esc(t):
    return (str(t) if t is not None else '').replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def _num(x):
    try:
        f = float(x)
    except (TypeError, ValueError):
        return str(x)
    return str(int(f)) if f == int(f) else f'{f:g}'


def _make_page_drawer(footer_note: str, user_code: str):
    def _draw(canvas, doc):
        canvas.saveState()
        y = BM - 6 * mm
        canvas.setStrokeColor(DIVIDER); canvas.setLineWidth(0.5)
        canvas.line(LM, y + 3 * mm, PAGE_W - RM, y + 3 * mm)
        canvas.setFont('Helvetica', 7); canvas.setFillColor(MUTED)
        left = f'{footer_note} · {user_code}' if footer_note else user_code
        canvas.drawString(LM, y, left)
        canvas.drawRightString(PAGE_W - RM, y, str(doc.page))
        canvas.restoreState()
    return _draw


def _no_pad(*extra):
    base = [('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 0),
            ('TOPPADDING', (0, 0), (-1, -1), 0), ('BOTTOMPADDING', (0, 0), (-1, -1), 0)]
    return TableStyle(base + list(extra))


# ── Banner ────────────────────────────────────────────────────────────────────
def _banner(branding):
    title = _esc(branding.get('report_title') or branding.get('header_left', 'Grade Report'))
    sub = _esc(branding.get('report_subtitle') or branding.get('header_right', ''))
    badge_txt = _esc(branding.get('badge_text', 'GRADE REPORT')).replace(' ', '<br/>')

    left = [Paragraph(title, S['banner_title'])]
    if sub:
        left.append(Spacer(1, 3))
        left.append(Paragraph(sub, S['banner_sub']))

    badge = Table([[Paragraph(badge_txt, S['badge'])]], colWidths=[30 * mm])
    badge.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, -1), ORANGE),
                               ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                               ('LEFTPADDING', (0, 0), (-1, -1), 4), ('RIGHTPADDING', (0, 0), (-1, -1), 4),
                               ('TOPPADDING', (0, 0), (-1, -1), 8), ('BOTTOMPADDING', (0, 0), (-1, -1), 8)]))

    t = Table([[left, badge]], colWidths=[CW - 34 * mm, 34 * mm])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), NAVY),
        ('VALIGN', (0, 0), (0, 0), 'MIDDLE'), ('VALIGN', (1, 0), (1, 0), 'MIDDLE'),
        ('ALIGN', (1, 0), (1, 0), 'RIGHT'),
        ('LEFTPADDING', (0, 0), (0, 0), 14), ('RIGHTPADDING', (1, 0), (1, 0), 12),
        ('TOPPADDING', (0, 0), (-1, -1), 12), ('BOTTOMPADDING', (0, 0), (-1, -1), 12)]))
    return t


# ── Score boxes ───────────────────────────────────────────────────────────────
def _score_box(label, num, denom):
    inner = Table([[Paragraph(label.upper(), S['box_label'])],
                   [Paragraph(_esc(num), S['box_num'])],
                   [Paragraph(denom, S['box_denom'])]])
    inner.setStyle(TableStyle([('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                               ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 0),
                               ('TOPPADDING', (0, 1), (0, 1), 2), ('BOTTOMPADDING', (0, 1), (0, 1), 2),
                               ('TOPPADDING', (0, 0), (0, 0), 2), ('BOTTOMPADDING', (0, 2), (0, 2), 2)]))
    box = Table([[inner]], colWidths=[CW * 0.485])
    box.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, -1), BOX_BG), ('BOX', (0, 0), (-1, -1), 1.1, NAVY),
                             ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                             ('TOPPADDING', (0, 0), (-1, -1), 12), ('BOTTOMPADDING', (0, 0), (-1, -1), 14)]))
    return box


def _score_boxes(total, total_max, normalize_to):
    norm = round(total / total_max * normalize_to, 1) if total_max else 0
    row = Table([[_score_box('Total Marks', _num(total), f'out of {_num(total_max)}'), '',
                  _score_box('Scaled Marks', _num(norm), f'out of {_num(normalize_to)}')]],
                colWidths=[CW * 0.485, CW * 0.03, CW * 0.485])
    row.setStyle(_no_pad())
    return row


# ── Section header bar ────────────────────────────────────────────────────────
def _header_bar(text):
    t = Table([[Paragraph(text.upper(), S['bar_header'])]], colWidths=[CW])
    t.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, -1), NAVY),
                           ('TOPPADDING', (0, 0), (-1, -1), 6), ('BOTTOMPADDING', (0, 0), (-1, -1), 6)]))
    return t


# ── Section-scores block (progress bars) ──────────────────────────────────────
def _progress_bar(pct, width):
    color = _bar_color(pct)
    filled = max(0.0, min(width, width * pct / 100.0))
    if filled <= 0.5:
        cells, widths, styles = [['']], [width], [('BACKGROUND', (0, 0), (0, 0), TRACK)]
    elif filled >= width - 0.5:
        cells, widths, styles = [['']], [width], [('BACKGROUND', (0, 0), (0, 0), color)]
    else:
        cells = [['', '']]; widths = [filled, width - filled]
        styles = [('BACKGROUND', (0, 0), (0, 0), color), ('BACKGROUND', (1, 0), (1, 0), TRACK)]
    t = Table(cells, colWidths=widths, rowHeights=[8])
    t.setStyle(_no_pad(*styles))
    return t


def _section_scores(sections):
    name_w, bar_w, score_w = CW * 0.34, CW * 0.44, CW * 0.22
    bar_track = bar_w - 10
    data, style = [], [
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING', (0, 0), (-1, -1), 10), ('RIGHTPADDING', (0, 0), (-1, -1), 10),
        ('TOPPADDING', (0, 0), (-1, -1), 8), ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ('LINEBELOW', (0, 0), (-1, -2), 0.4, DIVIDER),
    ]
    for i, s in enumerate(sections):
        score_cell = [Paragraph(f"{_num(s['score'])} / {_num(s['max'])}", S['sec_score']),
                      Paragraph(f"{round(s['pct'])}%", S['sec_pct'])]
        data.append([Paragraph(_esc(s['name']), S['sec_name']),
                     _progress_bar(s['pct'], bar_track), score_cell])
        if i % 2 == 1:
            style.append(('BACKGROUND', (0, i), (-1, i), ROW_ALT))
    t = Table(data, colWidths=[name_w, bar_w, score_w])
    t.setStyle(TableStyle(style))
    return t


# ── Section feedback block ────────────────────────────────────────────────────
def _feedback_block(sections):
    elems = []
    for i, s in enumerate(sections):
        hdr = Table([[Paragraph(_esc(s['full_title']), S['part_title']),
                      Paragraph(f"{_num(s['score'])} / {_num(s['max'])}", S['part_score'])]],
                    colWidths=[CW * 0.78, CW * 0.22])
        hdr.setStyle(_no_pad(('VALIGN', (0, 0), (-1, -1), 'TOP')))
        inner = [hdr, Spacer(1, 3)]
        if s.get('url'):
            inner.append(Paragraph(_esc(s['url']), S['repo_url']))
            inner.append(Spacer(1, 3))
        inner.append(Paragraph(s['feedback'] or 'No feedback available.', S['feedback']))
        if i == 0:
            # keep the "SECTION FEEDBACK" band glued to the first section so it can
            # never strand alone at the bottom of a page with content overflowing over
            inner = [_header_bar('Section Feedback'), Spacer(1, 8)] + inner
        elems.append(KeepTogether(inner))
        if i < len(sections) - 1:
            elems.append(HRFlowable(width=CW, thickness=0.4, color=DIVIDER, spaceBefore=7, spaceAfter=7))
    return elems


# ── Assemble one card ─────────────────────────────────────────────────────────
def _clean_feedback(raw):
    txt = re.sub(r'\[NOTE:[^\]]*\]\s*', '', raw or '').strip()
    esc = _esc(txt)
    # bold the single improvement header, then honour line breaks in the PDF paragraph
    for hdr in ("Areas for improvement:",):
        esc = esc.replace(_esc(hdr), f"<b>{_esc(hdr)}</b>")
    return esc.replace("\n", "<br/>")


_MISSING_NAME = {'', 'nan', 'none', 'null', 'na', 'n/a', '-', '--', 'tbu'}


def generate_pdf(row, out_path, reports_dir, part_defs, branding, total_max, normalize_to):
    user = row.get('User Code') or row.get('user_code') or 'unknown'
    total = float(row.get('Total_Score', 0) or 0)
    row_max = float(row.get('Total_Max', total_max) or total_max)

    sections = []
    for pk, label, short, pmax in part_defs:
        pmax_actual = float(row.get(f'{pk}_max') or pmax)
        score = float(row.get(f'{pk}_score') or 0)
        status = (row.get(f'{pk}_URL_status') or 'empty').strip()
        url = (row.get(f'{pk}_URL') or '').strip()
        fb = _clean_feedback(row.get(f'{pk}_feedback'))
        if not fb or fb.lower() in ('not submitted', 'not graded'):
            fb = 'No submission was received for this part.' if status == 'empty' else (fb or 'No feedback available.')
        pct = (score / pmax_actual * 100) if pmax_actual else 0
        display_url = ''
        if url and status != 'empty':
            display_url = url.replace('https://github.com/', 'github.com/').replace('https://', '')[:82]
        sections.append({
            'name': short, 'full_title': f'{label} — {short}',
            'score': score, 'max': pmax_actual, 'pct': pct,
            'feedback': fb, 'url': display_url,
        })

    doc = SimpleDocTemplate(str(out_path), pagesize=A4, leftMargin=LM, rightMargin=RM,
                            topMargin=TM, bottomMargin=BM,
                            title=f'Grade Report — {user}', author=branding.get('author', 'Grader'))

    raw_name = (row.get('Name') or '').strip()
    if raw_name.lower() in _MISSING_NAME:             # treat blank / spreadsheet junk as no name
        raw_name = ''
    name = _esc(raw_name)
    email = _esc((row.get('Email') or '').strip())
    big = name or _esc(user)                          # fall back to the student code as headline
    subbits = ([_esc(user)] if name else []) + ([email] if email else [])

    story = [
        _banner(branding),
        Spacer(1, 14),
        Paragraph('STUDENT', S['student_lbl']),
        Spacer(1, 2),
        Paragraph(big, S['student_code']),
    ]
    if subbits:
        story += [Spacer(1, 3), Paragraph('  ·  '.join(subbits), S['student_lbl'])]
    story += [
        Spacer(1, 14),
        _score_boxes(total, row_max, normalize_to),
        Spacer(1, 16),
        _header_bar('Section Scores'),
        Spacer(1, 2),
        _section_scores(sections),
        Spacer(1, 16),
    ]
    story.extend(_feedback_block(sections))

    drawer = _make_page_drawer(branding.get('footer_note', ''), user)
    doc.build(story, onFirstPage=drawer, onLaterPages=drawer)

# <<ENGINE_END>>


def run(run_dir: str, student: str = None):
    cfg = load_config(run_dir)
    cfg.paths.cards_dir.mkdir(parents=True, exist_ok=True)
    part_defs = cfg.part_defs()

    with open(cfg.paths.master_csv) as f:
        rows = list(csv.DictReader(f))
    if student:
        targets = {c.strip() for c in student.split(",")}
        rows = [r for r in rows if r["User Code"] in targets]

    print(f"Generating {len(rows)} report cards...")
    for i, row in enumerate(rows, 1):
        out = cfg.paths.cards_dir / f"{row['User Code']}.pdf"
        generate_pdf(row, out, cfg.paths.reports_dir, part_defs, cfg.branding,
                     cfg.total_marks, cfg.normalize_to)
        if i % 10 == 0 or i == len(rows):
            print(f"  {i}/{len(rows)}")
    print(f"\nAll PDFs in: {cfg.paths.cards_dir}/")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Generate PDF report cards")
    add_run_arg(ap)
    ap.add_argument("--student", type=str, help="Comma-separated User Codes (subset)")
    args = ap.parse_args()
    run(args.run, student=args.student)
