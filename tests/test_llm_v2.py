"""Tests for llm.py v2 — retry logic, semantic_review, synthesis_summary, backward compat."""

import json

import pytest

from unity_check.llm import (
    PROMPT_TEMPLATES,
    _call_llm_with_retry,
    evaluate_with_llm,
    semantic_review,
    synthesis_summary,
)


# ---------------------------------------------------------------------------
# _call_llm_with_retry
# ---------------------------------------------------------------------------
class TestCallLlmWithRetry:
    def test_success_first_attempt(self, monkeypatch):
        """Happy path: valid JSON returned on first try."""
        fake_response = type(
            "FakeResponse",
            (),
            {
                "choices": [
                    type(
                        "FakeChoice",
                        (),
                        {"message": type("FakeMsg", (), {"content": '{"key":"value"}'})()},
                    )()
                ],
                "usage": type("FakeUsage", (), {"total_tokens": 100})(),
            },
        )()

        calls = []

        class FakeClient:
            def __init__(self, *args, **kwargs):
                self.chat = type("FakeChat", (), {"completions": type("FakeCompletions", (), {})()})()

            @property
            def chat(self):
                return self._chat

            @chat.setter
            def chat(self, value):
                self._chat = value

        def fake_create(*, model, messages, temperature):
            calls.append(1)
            return fake_response

        monkeypatch.setattr("openai.OpenAI", lambda *a, **kw: None)
        monkeypatch.setattr(
            "unity_check.llm._build_client",
            lambda: type(
                "FakeClient",
                (),
                {
                    "chat": type(
                        "FakeChat",
                        (),
                        {"completions": type("FakeCompletions", (), {"create": staticmethod(fake_create)})()},
                    )()
                },
            )(),
        )

        result = _call_llm_with_retry("system prompt", "user content")
        assert result["result"] == {"key": "value"}
        assert result["tokens_used"] == 100
        assert result["duration_ms"] >= 0
        assert result["model_name"] == "deepseek-chat"
        assert len(calls) == 1

    def test_retry_on_json_parse_error(self, monkeypatch):
        """Invalid JSON on first attempt → retry succeeds on second."""

        attempts = []

        def fake_create(*, model, messages, temperature):
            attempts.append(1)
            if len(attempts) == 1:
                return type(
                    "FakeResponse",
                    (),
                    {
                        "choices": [
                            type(
                                "FakeChoice",
                                (),
                                {"message": type("FakeMsg", (), {"content": "not valid json"})()},
                            )()
                        ],
                        "usage": type("FakeUsage", (), {"total_tokens": 50})(),
                    },
                )()
            else:
                return type(
                    "FakeResponse",
                    (),
                    {
                        "choices": [
                            type(
                                "FakeChoice",
                                (),
                                {"message": type("FakeMsg", (), {"content": '{"retry":"ok"}'})()},
                            )()
                        ],
                        "usage": type("FakeUsage", (), {"total_tokens": 60})(),
                    },
                )()

        monkeypatch.setattr(
            "unity_check.llm._build_client",
            lambda: type(
                "FakeClient",
                (),
                {
                    "chat": type(
                        "FakeChat",
                        (),
                        {"completions": type("FakeCompletions", (), {"create": staticmethod(fake_create)})()},
                    )()
                },
            )(),
        )

        result = _call_llm_with_retry("sys", "user")
        assert result["result"] == {"retry": "ok"}
        assert len(attempts) == 2

    def test_exhausts_retries(self, monkeypatch):
        """All 3 attempts return invalid JSON → RuntimeError."""

        def fake_create(*, model, messages, temperature):
            return type(
                "FakeResponse",
                (),
                {
                    "choices": [
                        type(
                            "FakeChoice",
                            (),
                            {"message": type("FakeMsg", (), {"content": "garbage"})()},
                        )()
                    ],
                    "usage": type("FakeUsage", (), {"total_tokens": 10})(),
                },
            )()

        monkeypatch.setattr(
            "unity_check.llm._build_client",
            lambda: type(
                "FakeClient",
                (),
                {
                    "chat": type(
                        "FakeChat",
                        (),
                        {"completions": type("FakeCompletions", (), {"create": staticmethod(fake_create)})()},
                    )()
                },
            )(),
        )

        # Speed up retries for test.
        monkeypatch.setattr("unity_check.llm.RETRY_BACKOFF_BASE", 0.001)

        with pytest.raises(RuntimeError, match="exhausted"):
            _call_llm_with_retry("sys", "user")

    def test_retry_on_api_error(self, monkeypatch):
        """API error on first attempt → retry succeeds."""

        attempts = []

        def fake_create(*, model, messages, temperature):
            attempts.append(1)
            if len(attempts) == 1:
                raise ConnectionError("API down")
            return type(
                "FakeResponse",
                (),
                {
                    "choices": [
                        type(
                            "FakeChoice",
                            (),
                            {"message": type("FakeMsg", (), {"content": '{"recovered":true}'})()},
                        )()
                    ],
                    "usage": type("FakeUsage", (), {"total_tokens": 80})(),
                },
            )()

        monkeypatch.setattr(
            "unity_check.llm._build_client",
            lambda: type(
                "FakeClient",
                (),
                {
                    "chat": type(
                        "FakeChat",
                        (),
                        {"completions": type("FakeCompletions", (), {"create": staticmethod(fake_create)})()},
                    )()
                },
            )(),
        )
        monkeypatch.setattr("unity_check.llm.RETRY_BACKOFF_BASE", 0.001)

        result = _call_llm_with_retry("sys", "user")
        assert result["result"] == {"recovered": True}
        assert len(attempts) == 2


# ---------------------------------------------------------------------------
# semantic_review
# ---------------------------------------------------------------------------
class TestSemanticReview:
    def test_returns_findings(self, monkeypatch):
        """semantic_review should parse findings from LLM response."""
        r2_output = {
            "findings": [
                {
                    "title": "Unity anti-pattern",
                    "category": "unity_anti_pattern",
                    "severity": "high",
                    "description": "Using FindObjectOfType in Update",
                    "suggestion": "Cache the reference",
                }
            ]
        }

        monkeypatch.setattr(
            "unity_check.llm._call_llm_with_retry",
            lambda system_prompt, user_content, model_name=None: {
                "result": r2_output,
                "tokens_used": 500,
                "duration_ms": 2000,
                "model_name": "deepseek-chat",
            },
        )

        result = semantic_review(
            diff_content="+void Update() { var x = FindObjectOfType<Player>(); }",
            rule_results_summary={"total": 0},
            event_summary="push to main, commits=1",
        )
        assert len(result["findings"]) == 1
        assert result["findings"][0]["severity"] == "high"
        assert result["tokens_used"] == 500
        assert result["model_name"] == "deepseek-chat"

    def test_empty_findings_when_no_api_key(self, monkeypatch):
        """When LLM_API_KEY is empty, return empty findings + error note."""
        monkeypatch.setattr("unity_check.llm.get_settings", lambda: type(
            "FakeSettings",
            (),
            {"llm_api_key": "", "llm_model": "test-model", "llm_base_url": "http://x"},
        )())

        result = semantic_review("diff", {"total": 0}, "summary")
        assert result["findings"] == []
        assert result.get("error") == "LLM_API_KEY is empty"

    def test_handles_llm_failure(self, monkeypatch):
        """When _call_llm_with_retry raises, return empty findings + error."""
        monkeypatch.setattr(
            "unity_check.llm._call_llm_with_retry",
            lambda system_prompt, user_content, model_name=None: (_ for _ in ()).throw(
                RuntimeError("all retries exhausted")
            ),
        )

        result = semantic_review("diff", {"total": 0}, "summary")
        assert result["findings"] == []
        assert "all retries exhausted" in result.get("error", "")

    def test_findings_not_a_list_is_normalized(self, monkeypatch):
        """If LLM returns findings as non-list, normalize to empty list."""
        monkeypatch.setattr(
            "unity_check.llm._call_llm_with_retry",
            lambda system_prompt, user_content, model_name=None: {
                "result": {"findings": "not_a_list"},
                "tokens_used": 100,
                "duration_ms": 500,
                "model_name": "deepseek-chat",
            },
        )

        result = semantic_review("diff", {"total": 0}, "summary")
        assert result["findings"] == []


# ---------------------------------------------------------------------------
# synthesis_summary
# ---------------------------------------------------------------------------
class TestSynthesisSummary:
    def test_returns_complete_assessment(self, monkeypatch):
        """synthesis_summary should return all required fields."""
        r3_output = {
            "overall_score": 85.0,
            "risk_level": "low",
            "executive_summary": "Clean change.",
            "top_issues": [{"title": "Minor naming", "severity": "low", "source": "rule_check"}],
            "recommendation": "merge_ready",
            "action_items": [{"action": "Rename variable", "priority": "low"}],
        }

        monkeypatch.setattr(
            "unity_check.llm._call_llm_with_retry",
            lambda system_prompt, user_content, model_name=None: {
                "result": r3_output,
                "tokens_used": 600,
                "duration_ms": 1800,
                "model_name": "deepseek-chat",
            },
        )

        result = synthesis_summary(
            diff_content="+int x = 1;",
            rule_results_summary={"total": 1},
            r2_findings=[{"title": "ok"}],
            event_summary="push to main",
        )
        assert result["overall_score"] == 85.0
        assert result["risk_level"] == "low"
        assert result["recommendation"] == "merge_ready"
        assert len(result["top_issues"]) == 1
        assert len(result["action_items"]) == 1

    def test_handles_missing_r2_findings(self, monkeypatch):
        """When r2_findings is None, R3 still produces output."""
        monkeypatch.setattr(
            "unity_check.llm._call_llm_with_retry",
            lambda system_prompt, user_content, model_name=None: {
                "result": {
                    "overall_score": 60.0,
                    "risk_level": "high",
                    "executive_summary": "Only static analysis available.",
                    "top_issues": [],
                    "recommendation": "needs_review",
                    "action_items": [],
                },
                "tokens_used": 300,
                "duration_ms": 1000,
                "model_name": "deepseek-chat",
            },
        )

        result = synthesis_summary(
            diff_content="diff",
            rule_results_summary={"total": 0},
            r2_findings=None,
            event_summary="push",
        )
        assert result["risk_level"] == "high"
        assert result["recommendation"] == "needs_review"

    def test_no_api_key_returns_safe_defaults(self, monkeypatch):
        """When LLM_API_KEY is empty, return safe defaults."""
        monkeypatch.setattr("unity_check.llm.get_settings", lambda: type(
            "FakeSettings",
            (),
            {"llm_api_key": "", "llm_model": "test-model", "llm_base_url": "http://x"},
        )())

        result = synthesis_summary("diff", {"total": 0}, [], "summary")
        assert result["overall_score"] == 0.0
        assert result["risk_level"] == "unknown"
        assert result["recommendation"] == "needs_review"

    def test_handles_llm_failure(self, monkeypatch):
        """When _call_llm_with_retry raises, return safe defaults + error."""
        monkeypatch.setattr(
            "unity_check.llm._call_llm_with_retry",
            lambda system_prompt, user_content, model_name=None: (_ for _ in ()).throw(
                RuntimeError("API timeout")
            ),
        )

        result = synthesis_summary("diff", {"total": 0}, [], "summary")
        assert result["overall_score"] == 0.0
        assert result["risk_level"] == "unknown"
        assert "API timeout" in result.get("error", "")


# ---------------------------------------------------------------------------
# evaluate_with_llm backward compat
# ---------------------------------------------------------------------------
class TestEvaluateWithLlmBackwardCompat:
    def test_returns_legacy_format(self, monkeypatch):
        """evaluate_with_llm should return {risk_level, summary} dict."""
        monkeypatch.setattr(
            "unity_check.llm.semantic_review",
            lambda diff_content, rule_results_summary, event_summary: {
                "findings": [
                    {
                        "title": "Performance issue",
                        "severity": "high",
                        "category": "performance",
                        "description": "desc",
                        "suggestion": "fix",
                    }
                ],
                "tokens_used": 200,
                "duration_ms": 1000,
                "model_name": "deepseek-chat",
            },
        )

        result = evaluate_with_llm("push", None, "push to main", diff_content="+void Update()")
        assert "risk_level" in result
        assert "summary" in result
        assert result["risk_level"] == "high"
        assert "Performance issue" in result["summary"]

    def test_no_api_key_returns_unknown(self, monkeypatch):
        """When LLM_API_KEY is empty, legacy function returns unknown."""
        monkeypatch.setattr("unity_check.llm.get_settings", lambda: type(
            "FakeSettings",
            (),
            {"llm_api_key": "", "llm_model": "test-model", "llm_base_url": "http://x"},
        )())

        result = evaluate_with_llm("push", None, "summary")
        assert result["risk_level"] == "unknown"
        assert "LLM_API_KEY" in result["summary"]

    def test_no_findings_returns_low(self, monkeypatch):
        """When semantic_review returns no findings, risk is low."""
        monkeypatch.setattr(
            "unity_check.llm.semantic_review",
            lambda diff_content, rule_results_summary, event_summary: {
                "findings": [],
                "tokens_used": 100,
                "duration_ms": 500,
                "model_name": "deepseek-chat",
            },
        )

        result = evaluate_with_llm("push", None, "push to main")
        assert result["risk_level"] == "low"

    def test_semantic_review_error_propagates(self, monkeypatch):
        """When semantic_review returns error, legacy function reports it."""
        monkeypatch.setattr(
            "unity_check.llm.semantic_review",
            lambda diff_content, rule_results_summary, event_summary: {
                "findings": [],
                "error": "API crashed",
                "tokens_used": 0,
                "duration_ms": 0,
                "model_name": "",
            },
        )

        result = evaluate_with_llm("push", None, "summary")
        assert result["risk_level"] == "unknown"
        assert "API crashed" in result["summary"]


# ---------------------------------------------------------------------------
# Prompt templates
# ---------------------------------------------------------------------------
class TestPromptTemplates:
    def test_semantic_review_template_exists(self):
        assert "semantic_review" in PROMPT_TEMPLATES
        assert "Unity C#" in PROMPT_TEMPLATES["semantic_review"]

    def test_synthesis_template_exists(self):
        assert "synthesis_summary" in PROMPT_TEMPLATES
        assert "overall_score" in PROMPT_TEMPLATES["synthesis_summary"]

    def test_legacy_template_exists(self):
        assert "legacy_triage" in PROMPT_TEMPLATES
