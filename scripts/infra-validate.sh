#!/usr/bin/env bash
# Offline check for Issue 13 IaC. No Azure login. No spend.
# Usage (repo root): ./scripts/infra-validate.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

fail() {
  printf 'infra-validate: %s\n' "$*" >&2
  exit 1
}

if ! command -v bicep >/dev/null 2>&1; then
  fail "bicep CLI is required (https://github.com/Azure/bicep/releases)"
fi

OUT="${TMPDIR:-/tmp}/capitalcite-main.json"
bicep format infra/main.bicep
bicep build infra/main.bicep --outfile "${OUT}"
printf 'infra-validate: bicep build ok -> %s\n' "${OUT}"

python3 - "${OUT}" <<'PY'
import json, sys
from pathlib import Path

compiled = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
resources = compiled.get("resources", [])
types = [r.get("type", "") for r in resources]

def must_type(fragment: str) -> None:
    if not any(fragment in t for t in types):
        raise SystemExit(f"infra-validate: missing resource type containing {fragment!r}")

must_type("Microsoft.App/containerApps")
must_type("Microsoft.KeyVault/vaults")
must_type("Microsoft.ContainerRegistry/registries")
must_type("Microsoft.ManagedIdentity/userAssignedIdentities")

text = Path("infra/main.bicep").read_text(encoding="utf-8")
if "external: false" not in text and "external:false" not in text:
    raise SystemExit("infra-validate: Qdrant ingress must set external: false")
if "allowInsecure: false" not in text and "allowInsecure:false" not in text:
    raise SystemExit("infra-validate: API ingress must set allowInsecure: false")
if "adminUserEnabled: false" not in text:
    raise SystemExit("infra-validate: ACR admin user must stay disabled")
if "enableRbacAuthorization: true" not in text:
    raise SystemExit("infra-validate: Key Vault must use RBAC")
if "apiMinReplicas int = 0" not in text:
    raise SystemExit("infra-validate: API must default to scale-to-zero")
print("infra-validate: secure-default checks ok")
PY

if command -v checkov >/dev/null 2>&1; then
  checkov --framework bicep --quiet -f infra/main.bicep || fail "checkov reported findings"
  printf 'infra-validate: checkov ok\n'
else
  printf 'infra-validate: checkov UNVERIFIED (not installed)\n'
fi

if [[ "${SKIP_DOCKER:-}" == "1" ]]; then
  printf 'infra-validate: docker build skipped (SKIP_DOCKER=1)\n'
elif command -v docker >/dev/null 2>&1; then
  docker build -t capitalcite:infra-validate "${ROOT}"
  printf 'infra-validate: docker build ok\n'
else
  printf 'infra-validate: docker build UNVERIFIED (docker not installed)\n'
fi
