from __future__ import annotations

from importlib.metadata import version

from typer.testing import CliRunner

from capitalcite.cli import app

runner = CliRunner()


def test_cli_version() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert result.stdout.strip() == version("capitalcite")
