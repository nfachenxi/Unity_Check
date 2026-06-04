"""LLM integration: prompt templates, retry, multi-round evaluation functions.

Round 2: semantic_review() — architecture / design / Unity anti-patterns.
Round 3: synthesis_summary() — score, risk level, recommendation.
Backward-compat: evaluate_with_llm() kept with original signature.
"""

import json
import logging
import time
from typing import Any

from openai import OpenAI

from unity_check.config import get_settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------
MAX_DIFF_CHARS = 8000
MAX_RETRIES = 3
RETRY_BACKOFF_BASE = 2.0  # seconds: 2 → 4 → 8

# ---------------------------------------------------------------------------
# Prompt templates
# ---------------------------------------------------------------------------
PROMPT_TEMPLATES: dict[str, str] = {
    "semantic_review": (
        "You are an expert Unity C# code reviewer. Your job is to find **semantic** issues "
        "that static analysis tools miss — architectural risks, design-pattern misuse, "
        "Unity-specific anti-patterns, performance traps, and maintainability concerns.\n\n"
        "You will receive:\n"
        "1. A git diff of the code change.\n"
        "2. A summary of static-analysis rule violations already detected (Round 1).\n"
        "3. An event summary (push / PR context).\n\n"
        "**Do NOT re-report issues already covered by the static analysis rules.** "
        "Focus only on problems that require deeper understanding.\n\n"
        "Return **strict JSON** with this structure:\n"
        "{\n"
        '  "findings": [\n'
        "    {\n"
        '      "title": "Short descriptive title",\n'
        '      "category": "architecture|design|performance|unity_anti_pattern|maintainability|security",\n'
        '      "severity": "low|medium|high|critical",\n'
        '      "description": "What the issue is and why it matters",\n'
        '      "suggestion": "How to fix it",\n'
        '      "file": "optional file path if identifiable",\n'
        '      "line_hint": "optional line number or code snippet"\n'
        "    }\n"
        "  ]\n"
        "}\n"
        "If no semantic issues are found above and beyond the static rules, return an empty findings array."
    ),
    "synthesis_summary": (
        "You are a senior code-quality assessor. You will receive:\n"
        "1. A git diff of the code change.\n"
        "2. Round 1 results: static-analysis rule violations.\n"
        "3. Round 2 findings: semantic review discoveries (or a note that Round 2 failed).\n"
        "4. An event summary (push / PR context).\n\n"
        "Your job is to produce a **final holistic assessment**.\n\n"
        "Return **strict JSON** with this structure:\n"
        "{\n"
        '  "overall_score": <float 0-100, where 100 = perfect code>,\n'
        '  "risk_level": "low|medium|high|critical",\n'
        '  "executive_summary": "<2-4 sentence summary of the change quality>",\n'
        '  "top_issues": [\n'
        '    {"title": "...", "severity": "...", "source": "rule_check|semantic_review|both"}\n'
        "  ],  // max 5, ordered by severity\n"
        '  "recommendation": "merge_ready|needs_review|blocked",\n'
        '  "action_items": [\n'
        '    {"action": "...", "priority": "high|medium|low"}\n'
        "  ]  // max 5\n"
        "}\n\n"
        "Guidelines:\n"
        "- overall_score: deduct ~10-15 points per high-severity issue, ~5 for medium, ~2 for low.\n"
        "- risk_level: \"critical\" if any critical issue; \"high\" if >=3 high issues; "
        "\"medium\" if >=5 medium issues; else \"low\".\n"
        "- recommendation: \"blocked\" if critical/high risk and serious; "
        "\"needs_review\" if medium risk with important fixes; \"merge_ready\" if low risk.\n"
        "- top_issues: combine and de-duplicate the most important issues from both rounds.\n"
        "- If Round 2 failed, base your assessment on Round 1 and the diff alone, and note it in the summary."
    ),
    # Legacy prompt kept for backward-compatible evaluate_with_llm().
    "legacy_triage": (
        "You are a code review risk triage assistant. "
        "Return strict JSON with keys risk_level and summary. "
        "risk_level must be low, medium, high, or critical.\n"
        "event_type={event_type}\n"
        "action={action}\n"
        "event_summary={summary}\n"
    ),
}

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _build_client() -> OpenAI:
    settings = get_settings()
    return OpenAI(api_key=settings.llm_api_key, base_url=settings.llm_base_url)


def _call_llm_with_retry(
    system_prompt: str,
    user_content: str,
    model_name: str | None = None,
) -> dict[str, Any]:
    """Send an LLM request with up to 3 exponential-backoff retries.

    Parameters
    ----------
    system_prompt : str
        System-level instruction sent as the first message.
    user_content : str
        User message body (diff, summaries, etc.).
    model_name : str | None
        Override model; falls back to ``LLM_MODEL`` from settings.

    Returns
    -------
    dict
        ``result``: parsed JSON object.
        ``tokens_used``: total tokens consumed (prompt + completion).
        ``duration_ms``: wall-clock duration in milliseconds.
        ``model_name``: actual model used.

    Raises
    ------
    RuntimeError
        When all retries are exhausted (JSON parse failures or API errors).
    """
    settings = get_settings()
    model = model_name or settings.llm_model
    client = _build_client()
    last_error: str = ""

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            start = time.perf_counter()
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ],
                temperature=0.1,
            )
            elapsed_ms = int((time.perf_counter() - start) * 1000)

            content = response.choices[0].message.content or ""
            tokens = (response.usage.total_tokens if response.usage else 0)

            # Attempt JSON parse; retry if it fails.
            parsed = json.loads(content)
            return {
                "result": parsed,
                "tokens_used": tokens,
                "duration_ms": elapsed_ms,
                "model_name": model,
            }
        except json.JSONDecodeError as exc:
            last_error = f"JSON parse error (attempt {attempt}/{MAX_RETRIES}): {exc}"
            logger.warning(last_error)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_BASE ** attempt)
        except Exception as exc:
            last_error = f"LLM API error (attempt {attempt}/{MAX_RETRIES}): {exc}"
            logger.warning(last_error)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_BASE ** attempt)

    raise RuntimeError(f"LLM call exhausted {MAX_RETRIES} retries. Last error: {last_error}")


# ---------------------------------------------------------------------------
# Public API — Round 2: semantic review
# ---------------------------------------------------------------------------


def semantic_review(
    diff_content: str,
    rule_results_summary: dict[str, Any],
    event_summary: str,
) -> dict[str, Any]:
    """Run Round 2 LLM semantic evaluation.

    Parameters
    ----------
    diff_content : str
        The git diff content (may be truncated internally).
    rule_results_summary : dict
        Summary of Round 1 static-analysis results.
    event_summary : str
        Compact event description (push / PR context).

    Returns
    -------
    dict
        ``findings``, ``tokens_used``, ``duration_ms``, ``model_name``.
        On failure: empty findings + tokens_used=0 + ``error`` key.
    """
    settings = get_settings()
    if not settings.llm_api_key:
        return {
            "findings": [],
            "tokens_used": 0,
            "duration_ms": 0,
            "model_name": "",
            "error": "LLM_API_KEY is empty",
        }

    truncated = diff_content
    if len(truncated) > MAX_DIFF_CHARS:
        truncated = truncated[:MAX_DIFF_CHARS] + "\n... [diff truncated]"

    rule_json = json.dumps(rule_results_summary, ensure_ascii=False, indent=2)

    user_content = (
        f"=== EVENT SUMMARY ===\n{event_summary}\n\n"
        f"=== ROUND 1 — STATIC ANALYSIS RESULTS ===\n{rule_json}\n\n"
        f"=== CODE DIFF ===\n{truncated}\n"
    )

    try:
        call_result = _call_llm_with_retry(
            system_prompt=PROMPT_TEMPLATES["semantic_review"],
            user_content=user_content,
        )
        findings = call_result["result"].get("findings", [])
        if not isinstance(findings, list):
            findings = []
        return {
            "findings": findings,
            "tokens_used": call_result["tokens_used"],
            "duration_ms": call_result["duration_ms"],
            "model_name": call_result["model_name"],
        }
    except RuntimeError as exc:
        logger.exception("semantic_review failed: %s", exc)
        return {
            "findings": [],
            "tokens_used": 0,
            "duration_ms": 0,
            "model_name": settings.llm_model,
            "error": str(exc),
        }


# ---------------------------------------------------------------------------
# Public API — Round 3: synthesis summary
# ---------------------------------------------------------------------------


def synthesis_summary(
    diff_content: str,
    rule_results_summary: dict[str, Any],
    r2_findings: list[dict[str, Any]] | None,
    event_summary: str,
) -> dict[str, Any]:
    """Run Round 3 LLM synthesis: score, risk, recommendation.

    Parameters
    ----------
    diff_content : str
        The git diff content.
    rule_results_summary : dict
        Summary of Round 1 static-analysis results.
    r2_findings : list[dict] | None
        Round 2 findings.  ``None`` means Round 2 failed.
    event_summary : str
        Compact event description.

    Returns
    -------
    dict
        ``overall_score``, ``risk_level``, ``executive_summary``, ``top_issues``,
        ``recommendation``, ``action_items``, ``tokens_used``, ``duration_ms``,
        ``model_name``.  On failure: safe defaults + ``error`` key.
    """
    settings = get_settings()
    if not settings.llm_api_key:
        return {
            "overall_score": 0.0,
            "risk_level": "unknown",
            "executive_summary": "LLM_API_KEY is empty — evaluation skipped.",
            "top_issues": [],
            "recommendation": "needs_review",
            "action_items": [],
            "tokens_used": 0,
            "duration_ms": 0,
            "model_name": "",
            "error": "LLM_API_KEY is empty",
        }

    truncated = diff_content
    if len(truncated) > MAX_DIFF_CHARS:
        truncated = truncated[:MAX_DIFF_CHARS] + "\n... [diff truncated]"

    rule_json = json.dumps(rule_results_summary, ensure_ascii=False, indent=2)
    r2_json = (
        json.dumps(r2_findings, ensure_ascii=False, indent=2)
        if r2_findings is not None
        else "⚠ Round 2 (semantic review) FAILED — no semantic findings available."
    )

    user_content = (
        f"=== EVENT SUMMARY ===\n{event_summary}\n\n"
        f"=== ROUND 1 — STATIC ANALYSIS RESULTS ===\n{rule_json}\n\n"
        f"=== ROUND 2 — SEMANTIC REVIEW ===\n{r2_json}\n\n"
        f"=== CODE DIFF ===\n{truncated}\n"
    )

    try:
        call_result = _call_llm_with_retry(
            system_prompt=PROMPT_TEMPLATES["synthesis_summary"],
            user_content=user_content,
        )
        parsed = call_result["result"]
        return {
            "overall_score": float(parsed.get("overall_score", 0.0)),
            "risk_level": str(parsed.get("risk_level", "unknown")).lower(),
            "executive_summary": str(parsed.get("executive_summary", "")),
            "top_issues": parsed.get("top_issues", []) or [],
            "recommendation": str(parsed.get("recommendation", "needs_review")).lower(),
            "action_items": parsed.get("action_items", []) or [],
            "tokens_used": call_result["tokens_used"],
            "duration_ms": call_result["duration_ms"],
            "model_name": call_result["model_name"],
        }
    except RuntimeError as exc:
        logger.exception("synthesis_summary failed: %s", exc)
        return {
            "overall_score": 0.0,
            "risk_level": "unknown",
            "executive_summary": f"Synthesis failed: {exc}",
            "top_issues": [],
            "recommendation": "needs_review",
            "action_items": [],
            "tokens_used": 0,
            "duration_ms": 0,
            "model_name": settings.llm_model,
            "error": str(exc),
        }


# ---------------------------------------------------------------------------
# Backward-compatible wrapper
# ---------------------------------------------------------------------------


def evaluate_with_llm(
    event_type: str, action: str | None, summary: str, diff_content: str = ""
) -> dict[str, Any]:
    """Legacy single-round evaluation — kept for backward compatibility.

    Internally delegates to ``semantic_review`` and converts the findings
    into the old ``{risk_level, summary}`` format.  When Round 2 returns
    findings, risk is derived from the highest severity among them.
    """
    settings = get_settings()
    if not settings.llm_api_key:
        return {"risk_level": "unknown", "summary": "LLM_API_KEY is empty, skipped model evaluation."}

    # Build a minimal rule-summary (always empty for legacy path, since R1
    # wasn't separated before P3).
    rule_summary: dict[str, Any] = {"total": 0, "note": "legacy path — no Roslyn results available"}

    result = semantic_review(
        diff_content=diff_content,
        rule_results_summary=rule_summary,
        event_summary=(
            f"event_type={event_type}\n"
            f"action={action or 'none'}\n"
            f"event_summary={summary}\n"
        ),
    )

    if result.get("error"):
        return {"risk_level": "unknown", "summary": f"Semantic review failed: {result['error']}"}

    findings: list[dict[str, Any]] = result.get("findings", [])
    if not findings:
        return {"risk_level": "low", "summary": summary or "No semantic findings."}

    # Derive risk_level from the most severe finding.
    severity_order = {"critical": 4, "high": 3, "medium": 2, "low": 1}
    max_sev = "low"
    for f in findings:
        sev = str(f.get("severity", "low")).lower()
        if severity_order.get(sev, 0) > severity_order.get(max_sev, 0):
            max_sev = sev

    findings_summary = "; ".join(
        f"{f['title']} [{f.get('severity', '?')}]" for f in findings[:5]
    )
    return {"risk_level": max_sev, "summary": f"Findings: {findings_summary}"}
