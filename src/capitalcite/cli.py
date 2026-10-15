"""Command-line entry point.

Ingest, index, and eval subcommands land in later issues. This module exists
so `uv run capitalcite --help` works from day one.
"""

from __future__ import annotations

from importlib.metadata import version

import typer

app = typer.Typer(
    name="capitalcite",
    help="Cited, evaluated RAG over U.S. bank filings.",
    no_args_is_help=True,
    add_completion=False,
)


@app.callback()
def main() -> None:
    """Cited, evaluated RAG over U.S. bank filings.

    Ingest, index, and eval subcommands are added in later issues. The
    callback is here so a single `version` command stays a subcommand
    (Typer otherwise collapses one command into the program itself).
    """


@app.command("version")
def show_version() -> None:
    """Print the installed package version."""
    typer.echo(version("capitalcite"))
