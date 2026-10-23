from __future__ import annotations

import pytest
from pydantic import ValidationError

from capitalcite.ask.models import build_structured_model
from capitalcite.ask.types import ModelNotConfigured
from capitalcite.config import Settings


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
    assert settings.test_slow_retrieve_ms == 0


def test_slow_retrieve_ms_reads_capitalcite_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEC_USER_AGENT", "capitalcite-tests tests@example.com")
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("CAPITALCITE_TEST_SLOW_RETRIEVE_MS", "4000")
    settings = Settings()
    assert settings.test_slow_retrieve_ms == 4000


def test_slow_retrieve_ms_rejected_in_prod(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEC_USER_AGENT", "capitalcite-tests tests@example.com")
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("CAPITALCITE_TEST_SLOW_RETRIEVE_MS", "4000")
    with pytest.raises(ValidationError, match="CAPITALCITE_TEST_SLOW_RETRIEVE_MS"):
        Settings()


def test_slow_retrieve_ms_rejects_above_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEC_USER_AGENT", "capitalcite-tests tests@example.com")
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("CAPITALCITE_TEST_SLOW_RETRIEVE_MS", "10001")
    with pytest.raises(ValidationError):
        Settings()


def test_ask_model_openai_env_is_not_rewritten_to_fake(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEC_USER_AGENT", "capitalcite-tests tests@example.com")
    monkeypatch.setenv("ASK_MODEL", "openai")
    settings = Settings()
    assert settings.ask_model == "openai"
    with pytest.raises(ModelNotConfigured, match="OPENAI_API_KEY"):
        build_structured_model("openai", api_key=None, openai_model="gpt-4o-mini")


def test_cors_origins_strip_blanks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEC_USER_AGENT", "capitalcite-tests tests@example.com")
    monkeypatch.setenv("CORS_ORIGINS", " https://example.com , , https://other.example ")
    settings = Settings()
    assert settings.cors_origins == ("https://example.com", "https://other.example")
    monkeypatch.setenv("CORS_ORIGINS", " , ")
    settings = Settings()
    assert settings.cors_origins == ()


def test_cors_origins_reject_star(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEC_USER_AGENT", "capitalcite-tests tests@example.com")
    monkeypatch.setenv("CORS_ORIGINS", "*")
    with pytest.raises(ValidationError):
        Settings()
    monkeypatch.setenv("CORS_ORIGINS", "https://example.com,*")
    with pytest.raises(ValidationError):
        Settings()


def test_ask_rate_window_must_be_positive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEC_USER_AGENT", "capitalcite-tests tests@example.com")
    monkeypatch.setenv("ASK_RATE_WINDOW_S", "0")
    with pytest.raises(ValidationError):
        Settings()
    monkeypatch.setenv("ASK_RATE_WINDOW_S", "-1")
    with pytest.raises(ValidationError):
        Settings()


def test_trusted_proxy_hops_rejects_negative(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEC_USER_AGENT", "capitalcite-tests tests@example.com")
    monkeypatch.setenv("TRUSTED_PROXY_HOPS", "-1")
    with pytest.raises(ValidationError):
        Settings()


def test_ask_rate_limit_rejects_negative(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEC_USER_AGENT", "capitalcite-tests tests@example.com")
    monkeypatch.setenv("ASK_RATE_LIMIT", "-1")
    with pytest.raises(ValidationError):
        Settings()


def test_blank_git_sha_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEC_USER_AGENT", "capitalcite-tests tests@example.com")
    monkeypatch.setenv("GIT_SHA", "  ")
    settings = Settings()
    assert settings.git_sha is None


def test_policy_defaults_are_closed() -> None:
    settings = Settings.model_validate({"sec_user_agent": "capitalcite-tests tests@example.com"})
    assert settings.cors_origins == ()
    assert settings.ask_rate_limit == 0
    assert settings.ask_rate_window_s == 60.0
    assert settings.trusted_proxy_hops == 0
    assert settings.embed_threads == 0
    assert settings.git_sha is None


def test_env_example_defaults_ask_model_to_fake() -> None:
    from pathlib import Path

    text = (Path(__file__).resolve().parents[1] / ".env.example").read_text(encoding="utf-8")
    assert "\nASK_MODEL=fake\n" in text
    assert "\n# ASK_MODEL=openai\n" in text
    assert "\nASK_MODEL=openai\n" not in text
    assert "\nCORS_ORIGINS=\n" in text
    assert "\nASK_RATE_LIMIT=0\n" in text
    assert "\nASK_RATE_WINDOW_S=60\n" in text
    assert "\nTRUSTED_PROXY_HOPS=0\n" in text
    assert "\nEMBED_THREADS=0\n" in text
    assert "\nGIT_SHA=\n" in text
