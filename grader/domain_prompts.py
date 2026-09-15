"""Composable, auto-mapped domain grading modules.

The universal base prompt (grade.build_prompt) carries the strict, defect-hunting
grader *constitution* that applies to EVERY eval. A domain module layers the
domain-specific "what genuine work vs a hollow/broken submission looks like"
guidance on top. Modules are small and OPTIONAL: an eval whose domain we can't
resolve still gets the full strict base, so quality never depends on a module
existing.

Resolution order for an eval's domain:
  1. explicit   config_json['domain']            (set at intake / by a human)
  2. inferred   from the eval title + rubric text (keyword map below)
  3. None       -> base-only (still strict)

To add a domain: add one entry to DOMAIN_MODULES (+ optional inference keywords).
Nothing else changes — build_prompt injects whatever resolve() returns.
"""
from __future__ import annotations

# ── domain modules ───────────────────────────────────────────────────────────
# Each module is injected verbatim under a "## Domain grading guidance" heading.
# Keep them focused on what to VERIFY and what DEFECTS to catch in that domain —
# the universal rules (format, no-em-dash, no-silent-deductions) live in the base.

_DATA_ANALYTICS = (
    "This is a DATA / BUSINESS ANALYTICS deliverable (data cleaning, statistics, regression, "
    "KPI or A/B experiment analysis, dashboards and storytelling). Grade the ANALYSIS, not its "
    "mere presence:\n"
    "- Quantitative claims must be VERIFIABLE and internally consistent. Reported counts, summary "
    "statistics (means, R-squared, adjusted R-squared, p-values, z-scores, lift) and business figures "
    "must be plausible and must match the submitted data and the dataset brief. Cross-check specific "
    "numbers wherever you can.\n"
    "- Defects to catch and penalise (each in that section's deductions):\n"
    "    * figures computed on the WRONG dataset (row counts, column counts, date ranges, or group "
    "sizes that do not match the provided data);\n"
    "    * fabricated entities: a region, category, store type, or segment that does not exist in the "
    "data (a classic tell is a value copied from a generic sample dataset);\n"
    "    * implausible statistics: an R-squared near 1.0 on noisy real-world business data, or a "
    "single-variable R-squared that contradicts a relationship the data clearly shows;\n"
    "    * a regression shown only symbolically (no estimated coefficients, no R-squared) or "
    "'documented conceptually' — i.e. NOT actually run;\n"
    "    * an under-specified model where the rubric expects more (e.g. a single dummy variable when "
    "full region and store-type encoding is required);\n"
    "    * inconsistent target variables between the stated task and the executed model;\n"
    "    * dashboard or insight sections that state no real figures, or that are left as bracketed "
    "templates ([Value], [Region], [XX%]).\n"
    "- Reward genuine rigour: exact issue counts that reconcile with the data, the correct statistical "
    "test with a sound interpretation, specific and verifiable figures, correct dummy-variable handling "
    "with a stated reference category, residual analysis on named records, and honest limitations."
)

_DBMS = (
    "This is a DATABASE / SQL (DBMS) deliverable. Grade correctness and sound design, not presence:\n"
    "- Queries must be CORRECT for the stated task: verify that joins, filters, grouping and "
    "aggregation actually answer the question asked. A query that runs but returns the wrong result "
    "earns little.\n"
    "- Schema and design: check normalization claims against the actual schema, primary and foreign "
    "keys, and whether index or constraint choices are justified rather than merely asserted.\n"
    "- Defects to catch and penalise: queries that do not produce the required output; hard-coded or "
    "fabricated result sets instead of derived queries; missing constraints the task requires; and "
    "explanations that restate the task without evidence the query works.\n"
    "- Reward correct, efficient queries with sound justification and evidence (sample output) that "
    "they run as intended."
)

_MACHINE_LEARNING = (
    "This is a MACHINE LEARNING deliverable. Grade the modelling, not just that a notebook exists:\n"
    "- Verify the pipeline is sound: train/test split (no leakage), sensible preprocessing, a metric "
    "appropriate to the task, and reported scores that are plausible for the dataset.\n"
    "- Defects to catch and penalise: data leakage (fitting on test data, target leakage); reported "
    "accuracy or metrics that are implausible or unsupported by shown output; results claimed but not "
    "produced by the code; evaluation on the wrong split; and copy-paste analysis with no engagement "
    "with the actual results.\n"
    "- Reward correct methodology, honest evaluation with real numbers, error analysis, and clear "
    "reasoning about model choice and limitations."
)

_WEB_FRONTEND = (
    "This is a WEB FRONTEND deliverable. Grade the working UI and code quality, not just that files "
    "exist:\n"
    "- Verify the required features are actually implemented in the code (not just described in a "
    "README), that state and events are wired correctly, and that the layout matches the spec.\n"
    "- Defects to catch and penalise: features claimed in the README but absent from the code; "
    "hard-coded fake data where dynamic behaviour is required; broken or placeholder components; and "
    "accessibility or responsiveness the rubric requires but the code does not deliver.\n"
    "- Reward correct, working implementations with clean structure, real interactivity, and the "
    "specified responsive or accessible behaviour."
)

_BACKEND_API = (
    "This is a BACKEND / API deliverable. Grade correctness and design, not presence:\n"
    "- Verify endpoints actually implement the required behaviour: routing, request validation, the "
    "correct status codes, persistence, and auth where required — in the CODE, not just the docs.\n"
    "- Defects to catch and penalise: endpoints described but not implemented; missing validation or "
    "error handling the task requires; hard-coded responses instead of real logic; and insecure or "
    "absent auth where the rubric asks for it.\n"
    "- Reward correct, well-structured endpoints with proper validation, error handling, and evidence "
    "(tests or sample requests) that they work."
)

DOMAIN_MODULES: dict[str, str] = {
    "data-analytics": _DATA_ANALYTICS,
    "dbms": _DBMS,
    "machine-learning": _MACHINE_LEARNING,
    "web-frontend": _WEB_FRONTEND,
    "backend-api": _BACKEND_API,
}

# ── inference (fallback when no explicit domain is set) ───────────────────────
# Order matters: first domain whose keywords appear in (title + rubric) wins.
_INFER: list[tuple[tuple[str, ...], str]] = [
    (("sql", "dbms", "database", "normali", " join", "query", "schema", "index"), "dbms"),
    (("regression", "tableau", "dashboard", "kpi", "pivot", "hypothesis", "data cleaning",
      "business analyt", "experiment analysis", "a/b test", "statistic"), "data-analytics"),
    (("train", "model accuracy", "machine learning", "classifier", "neural", "sklearn",
      "confusion matrix", "feature engineering"), "machine-learning"),
    (("react", "css", "html", "frontend", "responsive", "component", "accessibility", "tailwind"),
     "web-frontend"),
    (("api", "endpoint", "fastapi", "express", "rest ", "backend", "authentication", "crud"),
     "backend-api"),
]


def resolve_domain(ev) -> str | None:
    """Explicit config domain → else infer from title+rubric → else None."""
    cfg = getattr(ev, "config_json", None) or {}
    d = str(cfg.get("domain") or "").strip().lower()
    if d in DOMAIN_MODULES:
        return d
    hay = " ".join([str(getattr(ev, "title", "") or ""),
                    str(getattr(ev, "rubric_md", "") or "")]).lower()
    for kws, dom in _INFER:
        if any(k in hay for k in kws):
            return dom
    return None


def domain_module(ev) -> tuple[str | None, str]:
    """Return (domain_key_or_None, module_text_or_empty) for an eval."""
    d = resolve_domain(ev)
    return d, DOMAIN_MODULES.get(d, "") if d else ""
