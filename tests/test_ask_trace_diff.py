from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check_ask_trace_diff.py"


def test_golden_ask_answers_match_with_tracing_on() -> None:
    env = os.environ.copy()
    env.pop("SEC_USER_AGENT", None)
    env["ASK_MODEL"] = "fake"
    completed = subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "items=112" in completed.stdout
    assert "mismatches=0" in completed.stdout
    assert "answered=" in completed.stdout
    answered = int(completed.stdout.strip().split("answered=")[-1].split()[0])
    assert answered >= 8


def test_trace_diff_refuses_a_live_model() -> None:
    env = os.environ.copy()
    env["ASK_MODEL"] = "openai"
    completed = subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 2
    assert "ASK_MODEL must be fake" in completed.stderr
