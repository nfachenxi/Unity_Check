"""LLM integration: prompt templates, retry, per-file dimension evaluation functions.

Provides two evaluation dimensions per .cs file:
- Dimension A: functionality_best_practices — 功能与Unity最佳实践
- Dimension B: security_performance_health — 安全、性能与工程健康度
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

_MD_FENCE_RE = __import__("re").compile(r"^```(?:json)?\s*\n(.*?)```\s*$", __import__("re").DOTALL)


def _strip_markdown_fences(text: str) -> str:
    """Remove a single outermost ```json / ``` fence if present."""
    s = text.strip()
    m = _MD_FENCE_RE.match(s)
    return m.group(1) if m else s

# ---------------------------------------------------------------------------
# Prompt templates
# ---------------------------------------------------------------------------
PROMPT_TEMPLATES: dict[str, str] = {
    "functionality_best_practices": (
        "You are an expert Unity C# code reviewer evaluating a single file's git diff.\n"
        "Your focus: **功能正确性与Unity最佳实践** (Functional Correctness & Unity Best Practices).\n\n"
        "Assess:\n"
        "1. Functional correctness — logic errors, incorrect assumptions, edge-case handling.\n"
        "2. Unity API usage — MonoBehaviour lifecycle (Awake/Start/Update/FixedUpdate/LateUpdate/OnEnable/OnDestroy), "
        "GameObject/Component patterns, Coroutine usage, Instantiate/Destroy patterns.\n"
        "3. Unity best practices — avoiding FindObjectOfType/GameObject.Find in hot paths, "
        "GetComponent caching, proper [SerializeField]/[HideInInspector] usage, "
        "object pooling awareness, Scene/Asset management patterns.\n"
        "4. Naming convention adherence — fields, methods, classes follow Unity/C# standards.\n\n"
        "**Do NOT re-report issues already caught by static analysis rules** "
        "(those are listed in the RULE RESULTS section). Focus on deeper, semantic issues.\n\n"
        "Return **strict JSON**:\n"
        "{\n"
        '  "score": <float 0-100, where 100 = no functional/best-practice issues>,\n'
        '  "summary": "<1-2 sentence summary in Chinese of the file quality from this dimension>",\n'
        '  "findings": [\n'
        "    {\n"
        '      "title": "Short descriptive title (use Chinese for major issues)",\n'
        '      "category": "best_practice|api_misuse|lifecycle|pattern|correctness",\n'
        '      "severity": "low|medium|high|critical",\n'
        '      "description": "What the issue is and why it matters (Chinese)",\n'
        '      "suggestion": "How to fix it (Chinese)",\n'
        '      "line_hint": "optional line reference"\n'
        "    }\n"
        "  ]\n"
        "}\n"
        "If no issues found, return an empty findings array and score 100."
    ),
    "security_performance_health": (
        "You are an expert Unity C# code reviewer evaluating a single file's git diff.\n"
        "Your focus: **安全、性能与工程健康度** (Security, Performance & Engineering Health).\n\n"
        "Assess:\n"
        "1. Security — input sanitization, injection risks, exposed sensitive data, "
        "Unity-specific risks (SendMessage/Invoke security, serialized field exposure, "
        "path traversal in asset loading).\n"
        "2. Performance — GC allocations (boxing/unboxing, excessive new in Update), "
        "string concatenation in loops, LINQ in hot paths, GetComponent/Find calls in "
        "Update/FixedUpdate, object instantiation frequency, yield return allocation patterns.\n"
        "3. Engineering health — SOLID principles, coupling/cohesion, error handling "
        "completeness, null-reference safety, IDisposable/resource cleanup, "
        "try-catch appropriate usage, logging quality.\n"
        "4. Code maintainability — method length, class responsibilities, magic numbers, "
        "hardcoded paths, configurable vs hardcoded values.\n\n"
        "**Do NOT re-report issues already caught by static analysis rules** "
        "(those are listed in the RULE RESULTS section). Focus on deeper, semantic issues.\n\n"
        "Return **strict JSON**:\n"
        "{\n"
        '  "score": <float 0-100, where 100 = no security/performance/health issues>,\n'
        '  "summary": "<1-2 sentence summary in Chinese of the file quality from this dimension>",\n'
        '  "findings": [\n'
        "    {\n"
        '      "title": "Short descriptive title (use Chinese for major issues)",\n'
        '      "category": "security|performance|maintainability|error_handling|resource_management|code_quality",\n'
        '      "severity": "low|medium|high|critical",\n'
        '      "description": "What the issue is and why it matters (Chinese)",\n'
        '      "suggestion": "How to fix it (Chinese)",\n'
        '      "line_hint": "optional line reference"\n'
        "    }\n"
        "  ]\n"
        "}\n"
        "If no issues found, return an empty findings array and score 100."
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

            content_stripped = _strip_markdown_fences(content)

            parsed = json.loads(content_stripped)
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
# Public API — per-file, per-dimension evaluation
# ---------------------------------------------------------------------------


def evaluate_file_dimension(
    file_path: str,
    file_diff: str,
    file_rule_results: list[dict[str, Any]],
    event_summary: str,
    dimension: str,
) -> dict[str, Any]:
    """Evaluate a single file's diff through one evaluation dimension.

    Parameters
    ----------
    file_path : str
        The relative path of the file being evaluated.
    file_diff : str
        The git diff content specific to this file (may be truncated).
    file_rule_results : list[dict]
        Roslyn static-analysis rule results filtered to this file.
    event_summary : str
        Compact event description (push / PR context).
    dimension : str
        ``"functionality_best_practices"`` or ``"security_performance_health"``.

    Returns
    -------
    dict
        ``score`` (float 0-100), ``summary`` (str), ``findings`` (list),
        ``tokens_used``, ``duration_ms``, ``model_name``.
        On failure: score=0, summary="评估失败", empty findings + ``error`` key.
    """
    settings = get_settings()
    if not settings.llm_api_key:
        return {
            "score": 0.0,
            "summary": "LLM_API_KEY 未配置",
            "findings": [],
            "tokens_used": 0,
            "duration_ms": 0,
            "model_name": "",
            "error": "LLM_API_KEY is empty",
        }

    if dimension not in PROMPT_TEMPLATES:
        return {
            "score": 0.0,
            "summary": f"未知维度: {dimension}",
            "findings": [],
            "tokens_used": 0,
            "duration_ms": 0,
            "model_name": "",
            "error": f"Unknown dimension: {dimension}",
        }

    truncated = file_diff
    if len(truncated) > MAX_DIFF_CHARS:
        truncated = truncated[:MAX_DIFF_CHARS] + "\n... [diff truncated]"

    rules_json = json.dumps(file_rule_results, ensure_ascii=False, indent=2) if file_rule_results else "无静态规则违规"

    user_content = (
        f"=== 文件路径 ===\n{file_path}\n\n"
        f"=== 事件摘要 ===\n{event_summary}\n\n"
        f"=== ROSLYN 规则结果 (本文件) ===\n{rules_json}\n\n"
        f"=== GIT DIFF (本文件) ===\n{truncated}\n"
    )

    try:
        call_result = _call_llm_with_retry(
            system_prompt=PROMPT_TEMPLATES[dimension],
            user_content=user_content,
        )
        parsed = call_result["result"]
        score = float(parsed.get("score", 0))
        summary = str(parsed.get("summary", ""))
        findings = parsed.get("findings", [])
        if not isinstance(findings, list):
            findings = []
        return {
            "score": score,
            "summary": summary,
            "findings": findings,
            "tokens_used": call_result["tokens_used"],
            "duration_ms": call_result["duration_ms"],
            "model_name": call_result["model_name"],
        }
    except RuntimeError as exc:
        logger.exception("evaluate_file_dimension(%s, %s) failed: %s", file_path, dimension, exc)
        return {
            "score": 0.0,
            "summary": "评估失败",
            "findings": [],
            "tokens_used": 0,
            "duration_ms": 0,
            "model_name": settings.llm_model,
            "error": str(exc),
        }
