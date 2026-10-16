from __future__ import annotations

from importlib.metadata import version

from typer.testing import CliRunner

from capitalcite.cli import app

runner = CliRunner()


def test_cli_help_lists_index() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "index" in result.stdout
    assert "ingest" in result.stdout
    assert "chunk" in result.stdout


def test_cli_version() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert result.stdout.strip() == version("capitalcite")
