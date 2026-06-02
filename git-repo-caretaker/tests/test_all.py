"""
tests/test_all.py – Basic unit tests for Git-Repo-Caretaker.

Run with: pytest tests/ -v
"""

import json
import pytest
from unittest.mock import MagicMock, patch


# ─── LLM Client ───────────────────────────────────────────────────────────────

class TestLLMClient:
    def test_call_llm_requires_api_key(self, monkeypatch):
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        from llm_client import call_llm
        with pytest.raises(EnvironmentError, match="OPENROUTER_API_KEY"):
            call_llm("hello")

    def test_call_llm_json_mode(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "choices": [{"message": {"content": '["bug", "enhancement"]'}}]
        }
        with patch("requests.post", return_value=mock_response):
            from llm_client import call_llm
            result = call_llm("classify this", json_mode=True)
        assert result == ["bug", "enhancement"]

    def test_call_llm_plain_text(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "Hello, world!"}}]
        }
        with patch("requests.post", return_value=mock_response):
            from llm_client import call_llm
            result = call_llm("say hello")
        assert result == "Hello, world!"


# ─── GitHub Client ────────────────────────────────────────────────────────────

class TestGitHubClient:
    def test_headers_raise_without_token(self, monkeypatch):
        monkeypatch.delenv("GH_TOKEN", raising=False)
        import importlib, github_client
        importlib.reload(github_client)
        with pytest.raises(EnvironmentError):
            github_client._headers()

    def test_get_file_content_404_returns_empty(self, monkeypatch):
        monkeypatch.setenv("GH_TOKEN", "fake")
        import requests as req
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        http_err = req.HTTPError(response=mock_resp)
        mock_resp.raise_for_status.side_effect = http_err
        with patch("requests.get", return_value=mock_resp):
            import github_client
            result = github_client.get_file_content("owner/repo", "missing.txt")
        assert result == ""


# ─── Triage Bot ───────────────────────────────────────────────────────────────

class TestTriageBot:
    def test_classify_returns_valid_labels(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "test")
        with patch("triage_bot.call_llm", return_value=["bug", "good first issue"]):
            from triage_bot import classify_issue
            labels = classify_issue("App crashes on startup", "Traceback (most recent call last)...")
        assert "bug" in labels

    def test_classify_fallback_on_error(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "test")
        with patch("triage_bot.call_llm", side_effect=Exception("LLM error")):
            from triage_bot import classify_issue
            labels = classify_issue("How do I install this?", "I tried pip install but it failed.")
        assert labels == ["question"]

    def test_suggest_assignee_returns_valid(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "test")
        maintainers = ["alice", "bob"]
        with patch("triage_bot.call_llm", return_value="alice"):
            from triage_bot import suggest_assignee
            result = suggest_assignee("Fix auth bug", "Login fails with 401", maintainers)
        assert result in maintainers

    def test_suggest_assignee_empty_list(self):
        from triage_bot import suggest_assignee
        result = suggest_assignee("title", "body", [])
        assert result is None


# ─── README Bot ──────────────────────────────────────────────────────────────

class TestReadmeBot:
    def test_levenshtein_ratio_identical(self):
        from readme_bot import _levenshtein_ratio
        assert _levenshtein_ratio("hello world", "hello world") == 1.0

    def test_levenshtein_ratio_empty(self):
        from readme_bot import _levenshtein_ratio
        assert _levenshtein_ratio("", "") == 1.0
        assert _levenshtein_ratio("hello", "") == 0.0

    def test_levenshtein_ratio_partial(self):
        from readme_bot import _levenshtein_ratio
        score = _levenshtein_ratio("hello world foo", "hello world bar")
        assert 0.4 < score < 1.0


# ─── Dependency Bot ───────────────────────────────────────────────────────────

class TestDependencyBot:
    def test_parse_requirements_txt(self):
        from dependency_bot import _parse_requirements_txt
        content = "requests==2.28.0\nnumpy>=1.24.0\n# comment\nflask==3.0.0"
        deps = _parse_requirements_txt(content)
        assert deps["requests"] == "2.28.0"
        assert deps["flask"] == "3.0.0"

    def test_parse_package_json(self):
        from dependency_bot import _parse_package_json
        content = json.dumps({
            "dependencies": {"react": "^18.2.0"},
            "devDependencies": {"jest": "29.0.0"}
        })
        deps = _parse_package_json(content)
        assert "react" in deps
        assert "jest" in deps

    def test_normalize_version(self):
        from dependency_bot import _normalize_version
        assert _normalize_version("^18.2.0") == "18.2.0"
        assert _normalize_version(">=2.28,<3.0") == "2.28"
        assert _normalize_version("~=1.4.2") == "1.4.2"

    def test_bump_requirements_txt(self):
        from dependency_bot import _bump_requirements_txt, Dependency
        content = "requests==2.28.0\nflask==2.0.0"
        dep = Dependency("requests", "2.28.0", "2.31.0", "python")
        result = _bump_requirements_txt(content, dep)
        assert "2.31.0" in result
        assert "flask==2.0.0" in result


# ─── FastAPI App ─────────────────────────────────────────────────────────────

class TestApp:
    def test_health_endpoint(self):
        from fastapi.testclient import TestClient
        from app import app
        client = TestClient(app)
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    def test_webhook_bad_signature(self):
        import os
        os.environ["GITHUB_WEBHOOK_SECRET"] = "secret123"
        from fastapi.testclient import TestClient
        from app import app
        client = TestClient(app)
        response = client.post(
            "/webhook",
            content=b'{"action": "opened"}',
            headers={
                "X-GitHub-Event": "issues",
                "X-Hub-Signature-256": "sha256=badsig",
                "Content-Type": "application/json",
            },
        )
        assert response.status_code == 401
