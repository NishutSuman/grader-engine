"""LLM-assisted rubric setup — analyze a question paper and, if it has no mark
scheme, generate a strict rubric from the evaluation's weightage / total marks.

The user pastes the question paper (single question or multi-part) into the
setup page. We use Claude to (a) detect whether a rubric/mark-scheme is already
present and map out the parts, and (b) on request, author a STRICT rubric in the
exact format `grader.intake.parse_rubric` understands:

    ## S1 — Title (18)
    - Criterion, objectively checkable — 3
    - ...

Model: claude-opus-4-8 (one-shot, quality-critical), adaptive thinking, with a
structured-output schema so the response is always valid JSON.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
_MODEL = os.environ.get("GRADEPILOT_RUBRIC_MODEL", "claude-opus-4-8")


# ── weightage → rubric posture policy ────────────────────────────────────────
# Difficulty lives in the QUESTIONS (set by the paper author, scaled to
# weightage). The rubric's job is not to add difficulty but to calibrate HOW
# marks are awarded — leniency, the bar for full marks, and discrimination —
# by weightage tier, with an unproctored-aware core that rewards hard-to-fake work.
_UNPROCTORED = (
    "These assignments are UNPROCTORED and take-home — students may use any "
    "references and AI tools. So every criterion must reward what is HARD TO "
    "FAKE: application to the SPECIFIC given problem/dataset/context (not generic "
    "textbook answers), shown working/reasoning, internal consistency (the "
    "conclusion follows from the student's own numbers), and citable evidence. A "
    "generic, correct-sounding but unsubstantiated answer must NOT earn full marks."
)

_NO_DOUBLE_DIFFICULTY = (
    "The QUESTION difficulty is already set by the paper author to match the "
    "weightage — do NOT make the tasks harder through the rubric, and do not "
    "invent requirements the paper didn't ask for. Calibrate only HOW marks are "
    "awarded: the bar for full marks, the granularity of criteria, and how much "
    "partial credit to give."
)

_POSTURE = {
    "light": ("Lenient · low-stakes",
        "This is a LOW-STAKES assignment (≤10% weightage); the paper is "
        "deliberately lighter. Keep the rubric LENIENT: reward a correct approach "
        "and completion, give generous partial credit, and use a few coarse "
        "criteria per part. Full marks for correct and complete work — do not "
        "demand exceptional depth."),
    "moderate": ("Balanced · moderate-stakes",
        "This is a MODERATE-STAKES assignment (>10–20% weightage); the paper is "
        "moderate-to-hard. Keep the rubric BALANCED: full marks require work that "
        "is correct, complete AND justified/applied to the given context; award "
        "moderate partial credit for correct-but-incomplete work."),
    "heavy": ("Discriminating · high-stakes",
        "This is a HIGH-STAKES assignment (>20% weightage) that gates program "
        "certification and placement eligibility; the paper is hard by design. "
        "Make the rubric DISCRIMINATING (not merely harsh): use fine-grained "
        "criteria, reserve full marks for work that is correct, complete, "
        "well-justified AND shows specific insight/depth, give little credit for "
        "generic or hand-wavy answers, and explicitly penalise unsubstantiated "
        "claims. The rubric must separate top performers from merely adequate ones."),
}


def weightage_pct(weightage: str):
    m = re.search(r"(\d+(?:\.\d+)?)", weightage or "")
    return float(m.group(1)) if m else None


def weightage_tier(weightage: str) -> dict:
    """Map a weightage string ('35%') → rubric posture tier + display label."""
    pct = weightage_pct(weightage)
    if pct is None:
        tier = "moderate"
    elif pct <= 10:
        tier = "light"
    elif pct <= 20:
        tier = "moderate"
    else:
        tier = "heavy"
    return {"pct": pct, "known": pct is not None, "tier": tier,
            "label": _POSTURE[tier][0]}


def _ensure_env() -> None:
    """The web app doesn't auto-load .env — pull ANTHROPIC_API_KEY from it."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        return
    envp = REPO / ".env"
    if envp.exists():
        for line in envp.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


def _client():
    _ensure_env()
    import anthropic
    return anthropic.Anthropic()


def _json_call(system: str, user: str, schema: dict, max_tokens: int) -> dict:
    """Truncation-guarded: if the response gets cut off mid-JSON
    (stop_reason == 'max_tokens'), retry with a larger budget instead of
    handing the caller a raw JSONDecodeError - the same guard grade.py's
    master grading path already has, just missing here until now. A detailed
    rubric + long problem statement can genuinely need more than the caller's
    baseline max_tokens, and adaptive-thinking tokens eat into that SAME
    budget, so a truncation was previously a hard, unrecoverable failure
    (confirmed live: atomize_rubric on iitp-aimlt-2601-81259 cut off at
    exactly its 6000-token budget, more than half consumed by thinking,
    producing "JSONDecodeError: Unterminated string...")."""
    text = ""
    for mt in (max_tokens, max(max_tokens * 2, 12000), max(max_tokens * 3, 20000)):
        msg = _client().messages.create(
            model=_MODEL,
            max_tokens=mt,
            thinking={"type": "adaptive"},
            system=system,
            output_config={"format": {"type": "json_schema", "schema": schema}},
            messages=[{"role": "user", "content": user}],
        )
        text = next((b.text for b in msg.content if b.type == "text"), "")
        if msg.stop_reason != "max_tokens":
            return json.loads(text)
    return json.loads(text)          # exhausted retries - raise with whatever we got


# ── analyze ──────────────────────────────────────────────────────────────────
_ANALYZE_SCHEMA = {
    "type": "object",
    "properties": {
        "rubric_present": {"type": "boolean"},
        "rubric_evidence": {"type": "string"},
        "parts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string"},
                    "title": {"type": "string"},
                    "marks": {"type": "number"},
                    "summary": {"type": "string"},
                },
                "required": ["label", "title", "marks", "summary"],
                "additionalProperties": False,
            },
        },
        "total_marks_detected": {"type": "number"},
        "notes": {"type": "string"},
    },
    "required": ["rubric_present", "rubric_evidence", "parts",
                 "total_marks_detected", "notes"],
    "additionalProperties": False,
}

_ANALYZE_SYS = (
    "You are an assessment-design expert. You are given the raw text of a "
    "question paper / graded assignment. Analyze it and report:\n"
    "1. rubric_present: true ONLY if the paper itself already contains a grading "
    "rubric or mark scheme — i.e. explicit criteria with marks/points, a "
    "breakdown of how marks are awarded, or a marking guide. Marks merely stated "
    "against a question (e.g. 'Q2 (10 marks)') is NOT a rubric.\n"
    "2. rubric_evidence: if present, quote/point to where it is; else ''.\n"
    "3. parts: the questions/parts. For each: a short label (e.g. 'Q1', 'Part 1', "
    "'S1'), a title, the marks stated for it (0 if none stated), and a one-line "
    "summary of what it asks.\n"
    "4. total_marks_detected: total marks stated in the paper (0 if none).\n"
    "5. notes: anything the grader should know (single-question vs multi-part, "
    "missing marks, ambiguities)."
)


def analyze_paper(problem_statement: str) -> dict:
    if not problem_statement.strip():
        raise ValueError("paste the question paper first")
    user = f"QUESTION PAPER:\n\n{problem_statement.strip()}"
    return _json_call(_ANALYZE_SYS, user, _ANALYZE_SCHEMA, max_tokens=4000)


# ── generate ─────────────────────────────────────────────────────────────────
_GENERATE_SCHEMA = {
    "type": "object",
    "properties": {
        "rubric_markdown": {"type": "string"},
        "total": {"type": "number"},
        "notes": {"type": "string"},
    },
    "required": ["rubric_markdown", "total", "notes"],
    "additionalProperties": False,
}

_GENERATE_SYS = (
    "You are an assessment-design expert who writes clear, fair, defensible "
    "grading rubrics. Given a question paper and a total-marks budget, author a "
    "rubric that distributes the marks across the paper's parts/questions with "
    "specific, objectively-checkable criteria a grader can verify against the "
    "submission with evidence (e.g. 'Normalises to 3NF with justification for "
    "each step — 4'). Avoid vague bands like 'good effort'.\n\n"
    "MARK DISTRIBUTION: respect any per-part marks stated in the paper. If the "
    "paper states none, distribute by the relative depth/importance of each part "
    "so the section maxes sum EXACTLY to the total budget. Each section's "
    "criteria marks must sum to that section's max.\n\n"
    "OUTPUT FORMAT — `rubric_markdown` must follow this EXACT shape so it parses "
    "downstream (one section per part; a leading short code, an em dash, the "
    "title, then the section marks in parentheses; criteria as bullets each "
    "ending with ' — <marks>'):\n\n"
    "## S1 — <Part title> (<section marks>)\n"
    "- <objective criterion> — <marks>\n"
    "- <objective criterion> — <marks>\n\n"
    "## S2 — <Part title> (<section marks>)\n"
    "- <objective criterion> — <marks>\n\n"
    "Use S1, S2, … (or the paper's own Q1/Part 1 labels) as the leading code. "
    "Section maxes must sum to the total budget; bullet marks must sum to each "
    "section max."
)


# ── extract (use the paper's OWN rubric, verbatim) ───────────────────────────
_EXTRACT_SYS = (
    "You are given a question paper that ALREADY contains its own grading rubric / "
    "mark scheme (criteria with marks — e.g. per-part 'Marks Breakdown' tables). "
    "TRANSCRIBE that existing rubric VERBATIM into the exact format below. You are "
    "copying, NOT designing: do not improve, tighten, reword into something "
    "stricter, or add anything.\n\n"
    "STRICT RULES (this rubric was shared with students — it must match what they "
    "were told):\n"
    "- Copy every criterion and its marks EXACTLY as the paper states them, keeping "
    "the paper's own wording and mark values.\n"
    "- Do NOT add criteria, requirements, hurdles, or extra specificity the paper "
    "did not state. Do NOT raise the bar for full marks. Do NOT remove or merge "
    "criteria.\n"
    "- One section per GRADABLE part. Skip any part that carries no marks and needs "
    "no submission (e.g. an intro/guidelines question). Section max = the part's "
    "stated marks; the section's bullet marks must sum to that max (the paper's "
    "breakdown already does).\n"
    "- If a part gives a total but does not mark its sub-points individually, keep "
    "the sub-points as bullets and split the stated marks as the paper implies; if "
    "truly unspecified, use one bullet equal to the part max.\n\n"
    "OUTPUT FORMAT — `rubric_markdown` must follow this EXACT shape so it parses "
    "downstream:\n\n"
    "## S1 — <Part title> (<section marks>)\n"
    "- <criterion, verbatim> — <marks>\n"
    "- <criterion, verbatim> — <marks>\n\n"
    "## S2 — <Part title> (<section marks>)\n"
    "- <criterion, verbatim> — <marks>\n\n"
    "Use the paper's own part labels (Part 1 / Q2 / S1) as the leading code. "
    "Section maxes must sum to the paper's total. In `notes`, state the total and "
    "confirm the rubric is the paper's own criteria transcribed verbatim (nothing "
    "added or tightened)."
)


def extract_rubric(problem_statement: str) -> dict:
    """Lift the paper's OWN rubric verbatim (for papers that already include one) —
    the fair/defensible default, since that rubric was shared with students. No
    added hurdles, unlike generate_rubric()."""
    if not problem_statement.strip():
        raise ValueError("paste the question paper first")
    user = ("Transcribe THIS paper's existing rubric verbatim into the required "
            "format:\n\n" + problem_statement.strip())
    res = _json_call(_EXTRACT_SYS, user, _GENERATE_SCHEMA, max_tokens=8000)
    res["mode"] = "verbatim"
    return res


def generate_rubric(problem_statement: str, total_marks: int,
                    weightage: str = "") -> dict:
    if not problem_statement.strip():
        raise ValueError("paste the question paper first")
    t = weightage_tier(weightage)
    posture = _POSTURE[t["tier"]][1]
    if not t["known"]:
        posture += " (Weightage was not specified — defaulting to a balanced posture.)"
    system = "\n\n".join([_GENERATE_SYS,
                          "RUBRIC POSTURE FOR THIS PAPER:\n" + posture,
                          _UNPROCTORED, _NO_DOUBLE_DIFFICULTY])
    wt = f"\nThis evaluation is worth {weightage} toward program certification." if weightage else ""
    user = (
        f"TOTAL MARKS BUDGET: {total_marks}{wt}\n\n"
        f"QUESTION PAPER:\n\n{problem_statement.strip()}\n\n"
        f"Write a rubric totalling exactly {total_marks} marks, matching the "
        f"posture above."
    )
    res = _json_call(system, user, _GENERATE_SCHEMA, max_tokens=8000)
    res["tier"] = t["tier"]
    res["posture_label"] = t["label"]
    return res


# ── atomise (decompose the rubric into granular, checkable atoms) ─────────────
# Consistency comes largely from the TASK being well-defined. A coarse criterion
# ("Data-quality audit — 5") forces a fuzzy 0–5 judgement (high variance); the
# same 5 marks split into near-binary atoms ("duplicates found — 1", "missing
# values with counts — 1", …) is far more reproducible. This decomposes the
# rubric for grader-internal use WITHOUT adding requirements (see rubric ethics).
_ATOMIZE_SYS = (
    "You refine a grading rubric for INTERNAL grader use. You are given a rubric "
    "whose criteria were SHARED WITH STUDENTS. Decompose each criterion into 2–4 "
    "GRANULAR, objectively-checkable atoms (near-binary: present / correct / "
    "shown-with-evidence) whose marks SUM EXACTLY to that criterion's marks. This "
    "makes grading consistent and defensible.\n\n"
    "HARD RULES — this rubric was disclosed to students, so you must NOT change what "
    "it asks:\n"
    "- Only DECOMPOSE what each criterion already requires. NEVER add a new "
    "requirement, raise the bar for full marks, or introduce anything students were "
    "not told.\n"
    "- Preserve every mark value: atoms sum to their criterion; criteria sum to the "
    "section max; sections sum to the total. Never move marks between criteria.\n"
    "- Each atom must be objectively checkable with evidence from the submission "
    "(prefer 'includes X, with Y shown' over vague 'good X').\n"
    "- If a dataset is provided, phrase data-related atoms as checks against the REAL "
    "data (e.g. 'reports the actual duplicate count / class balance').\n\n"
    "Return the atomised rubric as JSON. In `notes`, confirm the marks are preserved "
    "and nothing new was added."
)

_ATOMIZE_SCHEMA = {
    "type": "object",
    "properties": {
        "sections": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "key": {"type": "string"}, "title": {"type": "string"}, "max": {"type": "number"},
                "criteria": {"type": "array", "items": {
                    "type": "object",
                    "properties": {
                        "criterion": {"type": "string"}, "marks": {"type": "number"},
                        "atoms": {"type": "array", "items": {
                            "type": "object",
                            "properties": {"check": {"type": "string"}, "marks": {"type": "number"}},
                            "required": ["check", "marks"], "additionalProperties": False}},
                    },
                    "required": ["criterion", "marks", "atoms"], "additionalProperties": False}},
            },
            "required": ["key", "title", "max", "criteria"], "additionalProperties": False}},
        "total": {"type": "number"}, "notes": {"type": "string"},
    },
    "required": ["sections", "total", "notes"], "additionalProperties": False,
}


_TRAIL_MARK = re.compile(r"\s*[—–-]\s*([0-9]+(?:\.[0-9]+)?)\s*$")


def _split_mark(text: str):
    """Pull a trailing ` — <number>` off a line: returns (text_without_mark, mark)."""
    m = _TRAIL_MARK.search(text or "")
    if not m:
        return (text or "").strip(), None
    return text[:m.start()].strip(), float(m.group(1))


def _clean_criterion(text: str) -> str:
    """Drop a marks value the model sometimes echoes into the criterion string
    itself (the source of the ugly `— 3 — 3` double)."""
    stripped, _ = _split_mark(text)
    return stripped


def _atoms_md(sections: list) -> str:
    out = []
    for s in sections:
        out.append(f"## {s['title']} ({s['max']:g})")
        for c in s["criteria"]:
            out.append(f"- {_clean_criterion(c['criterion'])} — {c['marks']:g}")
            for a in c["atoms"]:
                out.append(f"    • {_clean_criterion(a['check'])} — {a['marks']:g}")
        out.append("")
    return "\n".join(out).strip()


def parse_atoms_md(md: str) -> dict:
    """Reverse of _atoms_md: parse an (edited) atomised rubric back into the
    structured form the grader actually consumes. So a human edit to the text
    changes grading, not just the display. Tolerant of the `— m — m` echo."""
    sections, cur_sec, cur_crit = [], None, None
    for raw in (md or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("## "):
            head = line[3:].strip()
            mm = re.search(r"\((\d+(?:\.\d+)?)\)\s*$", head)
            mx = float(mm.group(1)) if mm else 0.0
            title = re.sub(r"\s*\(\d+(?:\.\d+)?\)\s*$", "", head).strip()
            cur_sec = {"key": re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_")[:40] or "sec",
                       "title": title, "max": mx, "criteria": []}
            sections.append(cur_sec); cur_crit = None
        elif line.startswith("•") or line.startswith("- •") or line.lstrip("-").strip().startswith("•"):
            if cur_crit is not None:
                check, mk = _split_mark(line.lstrip("-").strip().lstrip("•").strip())
                cur_crit["atoms"].append({"check": _clean_criterion(check), "marks": mk or 0.0})
        elif line.startswith("- "):
            crit, mk = _split_mark(line[2:].strip())
            crit = _clean_criterion(crit)                # strip an echoed second mark
            if cur_sec is not None:
                cur_crit = {"criterion": crit, "marks": mk or 0.0, "atoms": []}
                cur_sec["criteria"].append(cur_crit)
    return {"sections": sections, "total": round(sum(s["max"] for s in sections), 2)}


def atomize_rubric(rubric_md: str, problem_statement: str = "",
                   dataset_brief: str = "", model: str = _MODEL) -> dict:
    """Decompose the (student-disclosed) rubric into granular, checkable atoms for
    grader-internal use — marks preserved, no new requirements. Returns the
    structure + a markdown rendering for review."""
    if not (rubric_md or "").strip():
        raise ValueError("no rubric to atomise — set up the rubric first")
    user = f"RUBRIC (shared with students):\n{rubric_md}\n\n"
    if problem_statement:
        # Atomization is a ONE-TIME, whole-eval call (unlike build_prompt's per-part,
        # per-student prompt, which truncates to 4000 chars because it runs hundreds
        # of times). A flat 4000-char cut here silently starved every part after the
        # first on any multi-part assignment whose combined problem statement ran
        # long — e.g. a 4-part eval where Part 2's task list didn't even start until
        # character 8779, so atomize_rubric never saw Parts 2-4 at all and reported
        # (accurately, but misleadingly) that they "couldn't be decomposed" — reading
        # like a rubric-quality problem when it was actually a truncation bug. Give
        # this call real headroom instead.
        user += f"PROBLEM STATEMENT:\n{problem_statement[:60000]}\n\n"
    if dataset_brief:
        user += f"DATASET (ground truth):\n{dataset_brief[:2000]}\n\n"
    user += "Decompose each criterion into checkable atoms, preserving all marks."
    res = _json_call(_ATOMIZE_SYS, user, _ATOMIZE_SCHEMA, max_tokens=6000)
    res["markdown"] = _atoms_md(res["sections"])
    return res
