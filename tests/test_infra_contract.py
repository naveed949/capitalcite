from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_dockerfile_does_not_copy_env() -> None:
    text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY .env" not in text
    assert "ASK_MODEL=fake" in text
    assert "uvicorn" in text
    assert "0.0.0.0" in text
    assert "tests/fixtures/chunking/corpus" in text
    assert "capitalcite chunk" in text
    assert "capitalcite index" in text
    assert "capitalcite ingest" not in text
    assert "FASTEMBED_CACHE_PATH" in text
    assert "QDRANT_PATH" in text
    assert "GIT_SHA" in text
    assert "BAAI/bge-small-en-v1.5" in text
    assert "Qdrant/bm25" in text
    assert "mock.file" in text
    assert "tamil.txt" in text
    assert "HF_HUB_OFFLINE=1" in text
    assert "ASK_RATE_LIMIT" in text
    assert "UV_PROJECT_ENVIRONMENT=/app/.venv" in text


def test_dockerignore_keeps_secrets_out_of_build_context() -> None:
    text = (ROOT / ".dockerignore").read_text(encoding="utf-8")
    assert ".env" in text
    assert ".git" in text
    assert "!tests/fixtures/chunking/corpus" in text


def test_make_image_passes_git_sha() -> None:
    text = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert "IMAGE ?=" in text
    assert "--build-arg GIT_SHA=" in text
    assert "UNVERIFIED: docker is not installed" in text


def test_bicep_keeps_qdrant_private_and_api_https() -> None:
    text = (ROOT / "infra" / "main.bicep").read_text(encoding="utf-8")
    bootstrap = (ROOT / "infra" / "bootstrap.bicep").read_text(encoding="utf-8")
    assert "external: false" in text
    assert "allowInsecure: false" in text
    assert "name: 'QDRANT_PATH'" in text
    assert "value: ''" in text
    assert text.index("name: 'QDRANT_URL'") < text.index("name: 'QDRANT_PATH'")
    assert "param apiMinReplicas int = 0" in text
    assert "adminUserEnabled: false" in bootstrap
    assert "enableRbacAuthorization: true" in bootstrap
    assert "federatedIdentityCredentials" in bootstrap
    assert "Microsoft.KeyVault/vaults" in bootstrap
    assert "Microsoft.Authorization/roleAssignments" in bootstrap


def test_compiled_main_json_has_no_role_assignments() -> None:
    source = (ROOT / "infra" / "main.bicep").read_text(encoding="utf-8")
    assert "Microsoft.Authorization/roleAssignments" not in source

    bicep = shutil.which("bicep")
    if bicep is None:
        pytest.skip("bicep CLI is not installed")

    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "main.json"
        subprocess.run(
            [bicep, "build", str(ROOT / "infra" / "main.bicep"), "--outfile", str(out)],
            check=True,
        )
        compiled_text = out.read_text(encoding="utf-8")
        compiled = json.loads(compiled_text)

    assert "Microsoft.Authorization/roleAssignments" not in compiled_text
    types = [r.get("type", "") for r in compiled.get("resources", [])]
    assert "Microsoft.Authorization/roleAssignments" not in types


def test_deploy_workflow_skips_without_secrets() -> None:
    text = (ROOT / ".github" / "workflows" / "deploy.yml").read_text(encoding="utf-8")
    assert "workflow_dispatch" in text
    assert "AZURE_CLIENT_ID" in text
    assert "UNVERIFIED" in text
    assert "skip=true" in text
    assert "pull_request:" not in text
    assert "v0.48.1" in text
    assert "apiImage=" in text
    assert "az containerapp update" not in text
    assert "releases/latest" not in text
    image_at = text.index("docker build")
    deploy_at = text.index("--template-file infra/main.bicep")
    assert image_at < deploy_at


def test_infra_workflow_does_not_login_to_azure() -> None:
    text = (ROOT / ".github" / "workflows" / "infra.yml").read_text(encoding="utf-8")
    assert "azure/login" not in text
    assert "bicep" in text
    assert "gitleaks" in text
    assert "make image" in text
    assert "IMAGE=capitalcite:ci" in text
    assert "docker build -t capitalcite:ci" not in text
    assert "v0.48.1" in text
    assert "releases/latest" not in text


def test_gitleaks_allowlist_covers_azure_role_ids() -> None:
    text = (ROOT / ".gitleaks.toml").read_text(encoding="utf-8")
    bicep = (ROOT / "infra" / "bootstrap.bicep").read_text(encoding="utf-8")
    for role_id in (
        "7f951dda-4ed3-4680-a7ca-43fe172d538d",
        "8311e382-0749-4cb8-b69d-38feb7eb23f0",
        "4633458b-17de-408a-b874-0445c86b69e6",
        "b24988ac-6180-42a0-ab88-20f7382dd24c",
    ):
        assert role_id in bicep
        assert role_id in text
        assert role_id not in (ROOT / "infra" / "main.bicep").read_text(encoding="utf-8")
