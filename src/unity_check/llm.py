import json
import logging
from typing import Any

from openai import OpenAI

from unity_check.config import get_settings

logger = logging.getLogger(__name__)

# Max characters of diff content fed to the LLM to stay within context window.
MAX_DIFF_CHARS = 8000


def evaluate_with_llm(
    event_type: str, action: str | None, summary: str, diff_content: str = ""
) -> dict[str, Any]:
    settings = get_settings()
    # Return deterministic fallback when key is missing to avoid breaking task flow.
    if not settings.llm_api_key:
        return {"risk_level": "unknown", "summary": "LLM_API_KEY is empty, skipped model evaluation."}

    client = OpenAI(api_key=settings.llm_api_key, base_url=settings.llm_base_url)
    prompt = (
        # Keep output contract strict so downstream storage and rendering remain stable.
        "You are a code review risk triage assistant. "
        "Return strict JSON with keys risk_level and summary. "
        "risk_level must be low, medium, high, or critical.\n"
        f"event_type={event_type}\n"
        f"action={action or 'none'}\n"
        f"event_summary={summary}\n"
    )

    if diff_content:
        truncated = diff_content
        if len(truncated) > MAX_DIFF_CHARS:
            truncated = truncated[:MAX_DIFF_CHARS] + "\n... [diff truncated]"
        prompt += (
            "\nCode diff (truncated):\n"
            f"{truncated}\n\n"
            "Analyze the code changes for:\n"
            "1. Unity-specific anti-patterns (Update/FixedUpdate misuse, "
            "FindObjectOfType in hot paths, etc.)\n"
            "2. C# best practices violations\n"
            "3. Performance risks\n"
            "4. Architecture concerns\n"
        )

    try:
        response = client.chat.completions.create(
            model=settings.llm_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
        )
        content = response.choices[0].message.content or ""
        parsed = json.loads(content)
        risk_level = str(parsed.get("risk_level", "unknown")).lower()
        result_summary = str(parsed.get("summary", "")).strip()
        return {"risk_level": risk_level, "summary": result_summary or "No summary returned."}
    except Exception as exc:
        logger.exception("Model evaluation failed: %s", exc)
        return {"risk_level": "unknown", "summary": f"Model evaluation failed: {exc}"}
