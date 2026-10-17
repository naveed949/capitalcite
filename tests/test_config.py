from __future__ import annotations

import pytest
from pydantic import ValidationError

from capitalcite.config import Settings
from capitalcite.ask.models import build_structured_model
from capitalcite.ask.types import ModelNotConfigured


def test_sec_user_agent_requires_contact_email(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEC_USER_AGENT", "capitalcite-without-email")
    with pytest.raises(ValidationError):
        Settings()


def test_sec_user_agent_accepts_name_and_email(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEC_USER_AGENT", "  Naveed Iqbal you@example.com  ")
    settings = Settings()
    assert settings.sec_user_agent == "Naveed Iqbal you@example.com"


def test_langfuse_sample_rate_rejects_out_of_range(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEC_USER_AGENT", "capitalcite-tests tests@example.com")
    monkeypatch.setenv("LANGFUSE_SAMPLE_RATE", "1.5")
    with pytest.raises(ValidationError):
        Settings()
    monkeypatch.setenv("LANGFUSE_SAMPLE_RATE", "-0.1")
    with pytest.raises(ValidationError):
        Settings()


def test_tracing_settings_defaults_and_blank_file() -> None:
    settings = Settings.model_validate(
        {
            "sec_user_agent": "capitalcite-tests tests@example.com",
            "langfuse_tracing_enabled": True,
        }
    )
    assert settings.langfuse_tracing_enabled is True
    assert settings.langfuse_sample_rate == 1.0
    assert settings.capitalcite_trace_file is None
    blank = Settings.model_validate(
        {
            "sec_user_agent": "capitalcite-tests tests@example.com",
            "capitalcite_trace_file": "  ",
            "langfuse_tracing_enabled": False,
            "langfuse_sample_rate": 0,
        }
    )
    assert blank.capitalcite_trace_file is None
    assert blank.langfuse_tracing_enabled is False
    assert blank.langfuse_sample_rate == 0


def test_qdrant_path_blank_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEC_USER_AGENT", "capitalcite-tests tests@example.com")
    monkeypatch.setenv("QDRANT_PATH", "  ")
    monkeypatch.setenv("QDRANT_URL", ":memory:")
    settings = Settings()
    assert settings.qdrant_path is None
    assert settings.qdrant_url == ":memory:"


def test_trace_file_blank_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEC_USER_AGENT", "capitalcite-tests tests@example.com")
    monkeypatch.setenv("CAPITALCITE_TRACE_FILE", "  ")
    settings = Settings()
    assert settings.capitalcite_trace_file is None


def test_langfuse_tracing_enabled_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEC_USER_AGENT", "capitalcite-tests tests@example.com")
    monkeypatch.setenv("LANGFUSE_TRACING_ENABLED", "true")
    settings = Settings()
    assert settings.langfuse_tracing_enabled is True
    monkeypatch.setenv("LANGFUSE_TRACING_ENABLED", "false")
    settings = Settings()
    assert settings.langfuse_tracing_enabled is False


def test_ask_model_defaults_to_fake() -> None:
    settings = Settings.model_validate({"sec_user_agent": "capitalcite-tests tests@example.com"})
    assert settings.ask_model == "fake"


def test_ask_model_openai_env_is_not_rewritten_to_fake(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEC_USER_AGENT", "capitalcite-tests tests@example.com")
    monkeypatch.setenv("ASK_MODEL", "openai")
    settings = Settings()
    assert settings.ask_model == "openai"
    with pytest.raises(ModelNotConfigured, match="OPENAI_API_KEY"):
        build_structured_model("openai", api_key=None, openai_model="gpt-4o-mini")


def test_env_example_defaults_ask_model_to_fake() -> None:
    from pathlib import Path

    text = (Path(__file__).resolve().parents[1] / ".env.example").read_text(encoding="utf-8")
    assert "\nASK_MODEL=fake\n" in text
    assert "\n# ASK_MODEL=openai\n" in text
    assert "\nASK_MODEL=openai\n" not in text
