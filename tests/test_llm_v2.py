"""Tests for llm.py v2 — retry logic, per-file dimension evaluation."""

import json

import pytest

from unity_check.llm import (
    PROMPT_TEMPLATES,
    _call_llm_with_retry,
    evaluate_file_dimension,
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

        def fake_create(*, model, messages, temperature):
            calls.append(1)
            return fake_response

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
# evaluate_file_dimension — dimension A
# ---------------------------------------------------------------------------
class TestEvaluateFileDimensionA:
    def test_returns_score_and_findings(self, monkeypatch):
        """evaluate_file_dimension with dimension A returns score + findings."""
        fake_output = {
            "score": 88.0,
            "summary": "代码质量良好，符合Unity最佳实践",
            "findings": [
                {
                    "title": "建议缓存GetComponent结果",
                    "category": "best_practice",
                    "severity": "medium",
                    "description": "在Update中调用GetComponent会产生性能开销",
                    "suggestion": "在Awake中缓存引用",
                }
            ],
        }

        monkeypatch.setattr(
            "unity_check.llm._call_llm_with_retry",
            lambda system_prompt, user_content, model_name=None: {
                "result": fake_output,
                "tokens_used": 500,
                "duration_ms": 2000,
                "model_name": "deepseek-chat",
            },
        )

        result = evaluate_file_dimension(
            file_path="Assets/Scripts/Player.cs",
            file_diff="+void Update() { GetComponent<Rigidbody>(); }",
            file_rule_results=[],
            event_summary="push to main, commits=1",
            dimension="functionality_best_practices",
        )
        assert result["score"] == 88.0
        assert result["summary"] and "Unity" in result["summary"]
        assert len(result["findings"]) == 1
        assert result["findings"][0]["severity"] == "medium"
        assert result["tokens_used"] == 500
        assert result["model_name"] == "deepseek-chat"

    def test_empty_findings_when_no_api_key(self, monkeypatch):
        """When LLM_API_KEY is empty, return safe defaults."""
        monkeypatch.setattr("unity_check.llm.get_settings", lambda: type(
            "FakeSettings",
            (),
            {"llm_api_key": "", "llm_model": "test-model", "llm_base_url": "http://x"},
        )())

        result = evaluate_file_dimension(
            file_path="X.cs", file_diff="diff",
            file_rule_results=[], event_summary="summary",
            dimension="functionality_best_practices",
        )
        assert result["score"] == 0.0
        assert result["findings"] == []
        assert result.get("error") == "LLM_API_KEY is empty"

    def test_handles_llm_failure(self, monkeypatch):
        """When _call_llm_with_retry raises, return safe defaults + error."""
        monkeypatch.setattr(
            "unity_check.llm._call_llm_with_retry",
            lambda system_prompt, user_content, model_name=None: (_ for _ in ()).throw(
                RuntimeError("all retries exhausted")
            ),
        )

        result = evaluate_file_dimension(
            file_path="X.cs", file_diff="diff",
            file_rule_results=[], event_summary="summary",
            dimension="functionality_best_practices",
        )
        assert result["score"] == 0.0
        assert "all retries exhausted" in result.get("error", "")

    def test_findings_not_a_list_is_normalized(self, monkeypatch):
        """If LLM returns findings as non-list, normalize to empty list."""
        monkeypatch.setattr(
            "unity_check.llm._call_llm_with_retry",
            lambda system_prompt, user_content, model_name=None: {
                "result": {"score": 80, "summary": "ok", "findings": "not_a_list"},
                "tokens_used": 100,
                "duration_ms": 500,
                "model_name": "deepseek-chat",
            },
        )

        result = evaluate_file_dimension(
            file_path="X.cs", file_diff="diff",
            file_rule_results=[], event_summary="summary",
            dimension="functionality_best_practices",
        )
        assert result["findings"] == []


# ---------------------------------------------------------------------------
# evaluate_file_dimension — dimension B
# ---------------------------------------------------------------------------
class TestEvaluateFileDimensionB:
    def test_returns_score_and_findings_b(self, monkeypatch):
        """evaluate_file_dimension with dimension B returns score + findings."""
        fake_output = {
            "score": 65.0,
            "summary": "存在性能和安全问题需要修复",
            "findings": [
                {
                    "title": "Update中频繁分配新对象",
                    "category": "performance",
                    "severity": "high",
                    "description": "每帧创建新的List对象导致GC压力",
                    "suggestion": "将List移到类字段并在Awake初始化",
                }
            ],
        }

        monkeypatch.setattr(
            "unity_check.llm._call_llm_with_retry",
            lambda system_prompt, user_content, model_name=None: {
                "result": fake_output,
                "tokens_used": 400,
                "duration_ms": 1500,
                "model_name": "deepseek-chat",
            },
        )

        result = evaluate_file_dimension(
            file_path="Assets/Scripts/Enemy.cs",
            file_diff="+void Update() { new List<int>(); }",
            file_rule_results=[{"rule_id": "CA1822", "severity": "Warning"}],
            event_summary="pull_request #5, action=opened",
            dimension="security_performance_health",
        )
        assert result["score"] == 65.0
        assert len(result["findings"]) == 1
        assert result["findings"][0]["severity"] == "high"
        assert result["tokens_used"] == 400

    def test_no_api_key_returns_defaults(self, monkeypatch):
        monkeypatch.setattr("unity_check.llm.get_settings", lambda: type(
            "FakeSettings",
            (),
            {"llm_api_key": "", "llm_model": "test-model", "llm_base_url": "http://x"},
        )())

        result = evaluate_file_dimension(
            file_path="X.cs", file_diff="diff",
            file_rule_results=[], event_summary="summary",
            dimension="security_performance_health",
        )
        assert result["score"] == 0.0
        assert result["findings"] == []

    def test_unknown_dimension(self, monkeypatch):
        """Unknown dimension returns error."""
        result = evaluate_file_dimension(
            file_path="X.cs", file_diff="diff",
            file_rule_results=[], event_summary="summary",
            dimension="nonexistent_dimension",
        )
        assert result["score"] == 0.0
        assert "Unknown dimension" in result.get("error", "")


# ---------------------------------------------------------------------------
# Prompt templates
# ---------------------------------------------------------------------------
class TestPromptTemplates:
    def test_dimension_a_template_exists(self):
        assert "functionality_best_practices" in PROMPT_TEMPLATES
        assert "Unity" in PROMPT_TEMPLATES["functionality_best_practices"]
        assert "功能" in PROMPT_TEMPLATES["functionality_best_practices"]

    def test_dimension_b_template_exists(self):
        assert "security_performance_health" in PROMPT_TEMPLATES
        assert "GC" in PROMPT_TEMPLATES["security_performance_health"]
        assert "安全" in PROMPT_TEMPLATES["security_performance_health"]

    def test_old_templates_removed(self):
        assert "semantic_review" not in PROMPT_TEMPLATES
        assert "synthesis_summary" not in PROMPT_TEMPLATES
        assert "legacy_triage" not in PROMPT_TEMPLATES
