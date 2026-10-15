"""Test process env. Loaded before test modules import the app."""

from __future__ import annotations

import os

os.environ.setdefault("SEC_USER_AGENT", "capitalcite-tests tests@example.com")
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("LOG_LEVEL", "WARNING")
