from __future__ import annotations

import pytest
from pydantic import ValidationError

from capitalcite.config import Settings


def test_sec_user_agent_requires_contact_email(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEC_USER_AGENT", "capitalcite-without-email")
    with pytest.raises(ValidationError):
        Settings()


def test_sec_user_agent_accepts_name_and_email(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEC_USER_AGENT", "  Naveed Iqbal you@example.com  ")
    settings = Settings()
    assert settings.sec_user_agent == "Naveed Iqbal you@example.com"
