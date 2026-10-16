#!/usr/bin/env bash
# Opt-in Azure apply. Skips with UNVERIFIED when az or a login is missing.
# Never treats a skipped cloud step as a pass.
# Usage (repo root): ./scripts/deploy.sh
# Optional env: AZURE_RESOURCE_GROUP, AZURE_LOCATION, PARAMS_FILE, API_IMAGE
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

log() {
  printf 'deploy: %s\n' "$*"
}

unverified() {
  log "UNVERIFIED: $*"
  exit 0
}

RG="${AZURE_RESOURCE_GROUP:-rg-capitalcite}"
LOCATION="${AZURE_LOCATION:-eastus}"
PARAMS="${PARAMS_FILE:-infra/parameters/demo.json}"

if ! command -v az >/dev/null 2>&1; then
  unverified "az CLI is not installed. Live Azure apply and /health on Azure stay unproven."
fi

if ! az account show >/dev/null 2>&1; then
  unverified "az is not logged in. Set GitHub OIDC secrets or run az login, then retry."
fi

if [[ ! -f "${PARAMS}" ]]; then
  log "missing parameters file ${PARAMS}" >&2
  exit 1
fi

if ! command -v bicep >/dev/null 2>&1 && ! az bicep version >/dev/null 2>&1; then
  unverified "bicep is not available to the logged-in az CLI."
fi

log "what-if on ${RG} using ${PARAMS}"
if ! az group show --name "${RG}" >/dev/null 2>&1; then
  log "creating resource group ${RG} in ${LOCATION}"
  az group create --name "${RG}" --location "${LOCATION}" --output none
fi

WHAT_IF_ARGS=(
  group what-if
  --resource-group "${RG}"
  --template-file infra/main.bicep
  --parameters "${PARAMS}"
)
if [[ -n "${API_IMAGE:-}" ]]; then
  WHAT_IF_ARGS+=(--parameters "apiImage=${API_IMAGE}")
fi

az "${WHAT_IF_ARGS[@]}"
log "what-if finished. apply is a second, explicit command:"
log "  az deployment group create --resource-group ${RG} --template-file infra/main.bicep --parameters ${PARAMS}"
log "Live /health on Azure is still UNVERIFIED until you curl the apiFqdn output."
