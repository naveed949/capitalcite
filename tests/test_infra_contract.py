"""Contracts for the Azure IaC and image recipe. No Azure account."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_dockerfile_does_not_copy_env() -> None:
    text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY .env" not in text
    assert "ASK_MODEL=fake" in text
    assert "uvicorn" in text
    assert "0.0.0.0" in text


def test_dockerignore_keeps_secrets_out_of_build_context() -> None:
    text = (ROOT / ".dockerignore").read_text(encoding="utf-8")
    assert ".env" in text
    assert ".git" in text


def test_bicep_keeps_qdrant_private_and_api_https() -> None:
    text = (ROOT / "infra" / "main.bicep").read_text(encoding="utf-8")
    assert "external: false" in text
    assert "allowInsecure: false" in text
    assert "adminUserEnabled: false" in text
    assert "enableRbacAuthorization: true" in text
    assert "param apiMinReplicas int = 0" in text
    assert "Microsoft.KeyVault/vaults" in text
    assert "federatedIdentityCredentials" in text


def test_deploy_workflow_skips_without_secrets() -> None:
    text = (ROOT / ".github" / "workflows" / "deploy.yml").read_text(encoding="utf-8")
    assert "workflow_dispatch" in text
    assert "AZURE_CLIENT_ID" in text
    assert "UNVERIFIED" in text
    assert "skip=true" in text
    assert "pull_request:" not in text


def test_infra_workflow_does_not_login_to_azure() -> None:
    text = (ROOT / ".github" / "workflows" / "infra.yml").read_text(encoding="utf-8")
    assert "azure/login" not in text
    assert "bicep" in text
    assert "gitleaks" in text
    assert "docker build" in text
