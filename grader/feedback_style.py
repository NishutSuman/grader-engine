"""Stage 2 of grading: rewrite the master grader's raw findings into learner-facing
feedback using `feedbackGenerationPrompt.txt` (repo root) VERBATIM as the system
prompt. The master grader (`grade.py`) still owns all scoring/rubric-evaluation/
deduction logic unchanged - this module only rephrases what it already found, per
the prompt's own framing: "grading... handled by a separate master prompt... your
role here is only to convert the evaluation findings provided to you".

Why a second LLM call and not more string templating: the prompt's rules (reframe
"you missed X" as "the section could have been strengthened by X", drop "you" from
improvement points, use "Opportunities to Further Strengthen" instead of "Areas for
improvement", etc.) require real paraphrasing that preserves meaning - not
achievable reliably with regex/string rules. This also RETIRES the old
`_crisp()`-driven character-budget truncation entirely for anything routed through
here, since the prompt has its own "1-2 sentences per bullet" conciseness rule
enforced by the model itself rather than a blunt post-hoc character cut.

One call per STUDENT (all of their parts at once), not one per part - the prompt is
built to take a whole multi-section evaluation and return one coherent document, and
batching this way is both cheaper and truer to its design than calling it per part.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Optional

REPO = Path(__file__).resolve().parent.parent
PROMPT_PATH = REPO / "feedbackGenerationPrompt.txt"

# Deliberately cheaper than the master grading model. This step only rephrases
# findings the master grader already computed (score/deductions/improvement are
# fixed inputs) into the required tone/structure - real paraphrasing, but not
# the rubric judgment call grading itself needs. Measured live: this step alone
# added ~11.5% to per-student cost when run on the same model as grading;
# Haiku is ~1/3 Sonnet's price, so this is the single highest-leverage cost
# lever found that doesn't touch grading accuracy at all (see grade._write_grades
# for the matching cost-accounting fix - it must price THIS model, not the
# grading model, or the logged cost silently overstates spend).
REWRITE_MODEL = "claude-haiku-4-5"



def _load_prompt() -> str:
    """Read fresh every call, never cached/copied elsewhere - the file IS the
    source of truth, so an edit to it takes effect on the very next grading call
    with no code change needed."""
    return PROMPT_PATH.read_text()


def _findings_block(title: str, sec: dict) -> str:
    """Serialize ONE section's raw master-grader findings into the plain-text
    'evaluation findings' input this prompt expects. This is OUR input framing,
    not the prompt itself - the prompt file is never modified."""
    score, mx = sec.get("score"), sec.get("max")
    lines = [f"[{title}] — {score:g}/{mx:g} marks" if score is not None and mx is not None else f"[{title}]",
             "", "What the evaluator found worked well:",
             (sec.get("feedback") or "").strip() or "(nothing recorded)", ""]
    deductions = sec.get("deductions") or []
    if deductions:
        lines.append("Improvement observations from the evaluator:")
        for d in deductions:
            reason = (d.get("reason") or d.get("criterion") or "").strip()
            if reason:
                lines.append(f"- {reason}")
        lines.append("")
    imp = (sec.get("improvement") or "").strip()
    if imp:
        lines.append("Evaluator's guidance on how this could have been strengthened:")
        lines.append(imp)
        lines.append("")
    return "\n".join(lines)


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", s.lower())


_ORDINAL_RE = re.compile(r"part\s*(\d+)", re.I)


def _ordinal(s: str) -> Optional[str]:
    m = _ORDINAL_RE.search(s)
    return m.group(1) if m else None


def _title_match(candidate_line: str, titles: list[str]) -> Optional[str]:
    """Match a header/anchor-window line back to one of our given titles. Tries
    the part NUMBER first ('Part 2' in the line == 'Part 2' in the title) -
    the model sometimes echoes an ABBREVIATED title ('[Part 1, Core App]'
    instead of the full 'Part 1 — Core App: Backend + Web Dashboard'), which
    breaks plain substring containment entirely (confirmed live). Falls back
    to substring containment for evals whose parts aren't numbered."""
    ord_ln = _ordinal(candidate_line)
    if ord_ln:
        hit = next((t for t in titles if _ordinal(t) == ord_ln), None)
        if hit:
            return hit
    norm_ln = _norm(candidate_line)
    return next((t for t in titles if _norm(t) and _norm(t) in norm_ln), None)


def _strip_markdown(text: str) -> str:
    """The PDF renderer (report_pdf.py) prints this text into a reportlab
    Paragraph with no markdown interpreter - a literal '**bold**' would show as
    literal asterisks in the student's PDF, not bold text. The prompt's Output
    Format doesn't call for markdown, but models add it by habit, so strip it
    defensively rather than rely on prompt wording (which must stay untouched)."""
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"(?<!\*)\*([^\s*][^*]*?)\*(?!\*)", r"\1", text)   # single-* italics
    return text


def _split_sections(text: str, titles: list[str]) -> tuple[dict[str, str], Optional[str]]:
    """Split the model's plain-text response back into {title: chunk} + an
    optional trailing Overall chunk, using the prompt's own stated structure
    (a header line, then 'What worked well') as the anchor. Best-effort: a title
    that can't be matched is simply absent from the returned dict, and the
    caller falls back to the old template for that one part.

    The returned body starts AT the 'What worked well' line, dropping the
    '[Section Name] — [Marks]' header line above it: our own PDF template
    already renders that section's title + score in its own header row
    (`_feedback_block` in report_pdf.py), so keeping the model's echoed header
    inside the stored text would show the title and marks twice."""
    lines = text.splitlines()
    # anchor on every line containing "What worked well" - preceded by that
    # section's header per the prompt's own Output Format spec, but models
    # inconsistently insert blank lines AND markdown separators ('---') between
    # the header and this line, so scan a small WINDOW above the anchor and
    # test each line individually for a title match, rather than assuming the
    # header is exactly the nearest non-blank line. Track the EXACT header line
    # index too (not just whether a match happened): a multi-section response
    # slices each part's body from ITS anchor up to the NEXT part's anchor, and
    # that range includes the next part's own header line ("[Part 2, ...], N/M
    # marks") unless we know precisely where that header starts and cut there
    # instead - otherwise it bleeds into the tail of the PREVIOUS part's stored
    # feedback (confirmed live on iitp-sdaieng-2602-81303's first 3-part run).
    anchors = [i for i, ln in enumerate(lines) if "what worked well" in ln.lower()]
    match_of, header_idx_of = {}, {}                # anchor_line_idx -> (matched title, header line idx)
    for i in anchors:
        for j in range(i - 1, max(-1, i - 7), -1):   # scan backward through the lookback window
            if not lines[j].strip():
                continue
            hit = _title_match(lines[j], titles)
            if hit:
                match_of[i] = hit
                header_idx_of[i] = j
                break
    # 'Overall' line, tolerant of markdown decoration ('**Overall**', '## Overall',
    # a leading '- ', etc.) - normalize first, same as the title matching above.
    overall_idx = next((i for i, ln in enumerate(lines) if _norm(ln) == "overall"), None)
    end_bound = overall_idx if overall_idx is not None else len(lines)
    starts = [i for i in anchors if i < end_bound] + [end_bound]

    chunks: dict[str, str] = {}
    for a, b in zip(starts, starts[1:]):
        match = match_of.get(a)
        if not match and len(titles) == 1:          # single-section eval: no ambiguity, no header needed
            match = titles[0]
        if match:
            cut = header_idx_of.get(b, b)            # stop before the NEXT part's header, not at its anchor
            chunk_lines = lines[a:cut]
            while chunk_lines and (not chunk_lines[-1].strip() or not _norm(chunk_lines[-1])):
                chunk_lines.pop()                    # trailing blank / separator-only line before a cut
            chunks[match] = _strip_markdown("\n".join(chunk_lines).strip())

    overall_text = None
    if overall_idx is not None:
        overall_text = _strip_markdown("\n".join(lines[overall_idx + 1:]).strip()) or None
    return chunks, overall_text


def rewrite_student_feedback(client, model: str, parts, sections: dict[str, dict],
                              overall_raw: str = "") -> tuple[dict[str, str], Optional[str], Optional[dict]]:
    """parts: ordered list of Part-like objects (.key, .title). sections: {part.key:
    sec} the master grader's raw per-part findings. `model` is accepted for call-site
    compatibility but NOT used for the actual API call - this step always runs on
    REWRITE_MODEL (see its comment for why), independent of whatever model graded
    the student. Returns ({part.key: rewritten learner-facing text},
    overall_text_or_None, usage_row_or_None). usage_row is the real token usage of
    THIS call ({in, out, cr, cw} - see grade._usage_row) so the caller can fold it
    into the student's tracked cost (priced at REWRITE_MODEL's rate, not `model`'s);
    None if the call never completed. Never raises - a failure at any stage (API
    error, unparseable response) returns ({}, None, None) so the caller can fall
    back to the existing template per part."""
    ordered = [p for p in parts if p.key in sections]
    if not ordered:
        return {}, None, None
    blocks = [_findings_block(p.title, sections[p.key]) for p in ordered]
    findings = "\n\n".join(blocks)
    if overall_raw.strip():
        findings += f"\n\n[Overall]\n{overall_raw.strip()}\n"
    try:
        msg = client.with_options(timeout=120.0).messages.create(
            model=REWRITE_MODEL, max_tokens=4000,
            system=_load_prompt(),
            messages=[{"role": "user", "content":
                       "Evaluation findings to convert into learner-facing feedback:\n\n" + findings}])
        text = "".join(b.text for b in msg.content if getattr(b, "type", None) == "text")
        usage_row = {"in": msg.usage.input_tokens, "out": msg.usage.output_tokens,
                     "cr": getattr(msg.usage, "cache_read_input_tokens", 0) or 0,
                     "cw": getattr(msg.usage, "cache_creation_input_tokens", 0) or 0}
    except Exception:
        return {}, None, None
    if not text.strip():
        return {}, None, usage_row
    title_to_key = {p.title: p.key for p in ordered}
    chunks, overall_text = _split_sections(text, list(title_to_key))
    by_key = {title_to_key[t]: body for t, body in chunks.items()}
    return by_key, overall_text, usage_row
