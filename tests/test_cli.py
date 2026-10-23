from __future__ import annotations

import shutil
import subprocess
from importlib.metadata import version
from pathlib import Path

import pytest
from typer.testing import CliRunner

from capitalcite.cli import app

REPO = Path(__file__).resolve().parents[1]
runner = CliRunner()


def test_cli_help_without_repo_root(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("capitalcite.cli._repo_root", lambda: None)
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "metrics" in result.stdout


def test_cli_fixtures_exit_1_when_tree_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("capitalcite.cli._repo_root", lambda: None)
    result = runner.invoke(
        app,
        [
            "metrics",
            "extract",
            "--fixtures",
            "--tickers",
            "JPM",
            "--output-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 1
    assert "error:" in result.stdout + result.stderr
    assert "tests/fixtures/metrics" in result.stdout + result.stderr
    assert list(tmp_path.rglob("*")) == []


def test_cli_help_lists_index() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "index" in result.stdout
    assert "ingest" in result.stdout
    assert "chunk" in result.stdout
    assert "eval" in result.stdout
    assert "audit" in result.stdout
    assert "check" in result.stdout
    assert "mcp" in result.stdout
    assert "metrics" in result.stdout


def test_bankfind_user_agent_falls_back_without_package(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from importlib.metadata import PackageNotFoundError

    from capitalcite.agent.fdic import bankfind_user_agent

    def missing(_name: str) -> str:
        raise PackageNotFoundError("capitalcite")

    monkeypatch.setattr("capitalcite.agent.fdic.version", missing)
    assert bankfind_user_agent() == "capitalcite"


def test_cli_version() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert result.stdout.strip() == version("capitalcite")


def test_packaged_cli_help_does_not_need_the_checkout(tmp_path: Path) -> None:
    uv = shutil.which("uv")
    assert uv is not None
    venv = tmp_path / "venv"
    subprocess.run([uv, "venv", str(venv)], check=True, cwd=tmp_path)
    python = venv / "bin" / "python"
    subprocess.run(
        [uv, "pip", "install", "--python", str(python), str(REPO)],
        check=True,
    )
    result = subprocess.run(
        [str(venv / "bin" / "capitalcite"), "--help"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "ingest" in result.stdout
    assert "metrics" in result.stdout
