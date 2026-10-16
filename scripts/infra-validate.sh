#!/usr/bin/env bash
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

TMP="${TMPDIR:-/tmp}/capitalcite-bicep"
mkdir -p "${TMP}"
MAIN_OUT="${TMP}/main.json"
BOOT_OUT="${TMP}/bootstrap.json"

bicep format infra/bootstrap.bicep
bicep format infra/main.bicep
bicep build infra/bootstrap.bicep --outfile "${BOOT_OUT}"
bicep build infra/main.bicep --outfile "${MAIN_OUT}"
printf 'infra-validate: bicep build ok -> %s %s\n' "${BOOT_OUT}" "${MAIN_OUT}"

python3 - "${BOOT_OUT}" "${MAIN_OUT}" <<'PY'
import json, sys
from pathlib import Path

boot_path, main_path = Path(sys.argv[1]), Path(sys.argv[2])
bootstrap = json.loads(boot_path.read_text(encoding="utf-8"))
main = json.loads(main_path.read_text(encoding="utf-8"))


def types_of(compiled: dict) -> list[str]:
    return [r.get("type", "") for r in compiled.get("resources", [])]


def must_type(types: list[str], fragment: str, label: str) -> None:
    if not any(fragment in t for t in types):
        raise SystemExit(f"infra-validate: {label} missing resource type containing {fragment!r}")


boot_types = types_of(bootstrap)
main_types = types_of(main)

must_type(boot_types, "Microsoft.ManagedIdentity/userAssignedIdentities", "bootstrap")
must_type(boot_types, "Microsoft.KeyVault/vaults", "bootstrap")
must_type(boot_types, "Microsoft.ContainerRegistry/registries", "bootstrap")
must_type(boot_types, "Microsoft.Authorization/roleAssignments", "bootstrap")
must_type(main_types, "Microsoft.App/containerApps", "main")

if any("Microsoft.Authorization/roleAssignments" in t for t in main_types):
    raise SystemExit("infra-validate: compiled main.json must not contain Microsoft.Authorization/roleAssignments")
main_text = main_path.read_text(encoding="utf-8")
if "Microsoft.Authorization/roleAssignments" in main_text:
    raise SystemExit("infra-validate: compiled main.json text must not mention Microsoft.Authorization/roleAssignments")

source_main = Path("infra/main.bicep").read_text(encoding="utf-8")
source_boot = Path("infra/bootstrap.bicep").read_text(encoding="utf-8")
if "Microsoft.Authorization/roleAssignments" in source_main:
    raise SystemExit("infra-validate: main.bicep must not declare role assignments")
if "Microsoft.Authorization/roleAssignments" not in source_boot:
    raise SystemExit("infra-validate: bootstrap.bicep must declare role assignments")
if "federatedIdentityCredentials" not in source_boot:
    raise SystemExit("infra-validate: bootstrap.bicep must declare GitHub federated credentials")
if "external: false" not in source_main and "external:false" not in source_main:
    raise SystemExit("infra-validate: Qdrant ingress must set external: false")
if "allowInsecure: false" not in source_main and "allowInsecure:false" not in source_main:
    raise SystemExit("infra-validate: API ingress must set allowInsecure: false")
if "adminUserEnabled: false" not in source_boot:
    raise SystemExit("infra-validate: ACR admin user must stay disabled")
if "enableRbacAuthorization: true" not in source_boot:
    raise SystemExit("infra-validate: Key Vault must use RBAC")
if "apiMinReplicas int = 0" not in source_main:
    raise SystemExit("infra-validate: API must default to scale-to-zero")
print("infra-validate: secure-default checks ok")
PY

if command -v checkov >/dev/null 2>&1; then
  checkov --framework bicep --quiet -f infra/bootstrap.bicep -f infra/main.bicep || fail "checkov reported findings"
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
