#!/usr/bin/env bash
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

if ! az group show --name "${RG}" >/dev/null 2>&1; then
  unverified "resource group ${RG} is missing. Apply infra/bootstrap.bicep as Owner first (make bootstrap)."
fi

if ! az deployment group show --resource-group "${RG}" --name bootstrap >/dev/null 2>&1; then
  unverified "deployment 'bootstrap' is missing. Apply infra/bootstrap.bicep as Owner first (make bootstrap)."
fi

log "what-if on ${RG} using ${PARAMS}"
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
log "what-if finished. Push the image, then apply main.bicep with that tag:"
log "  az deployment group create --name capitalcite --resource-group ${RG} --template-file infra/main.bicep --parameters ${PARAMS} --parameters apiImage=<acr>/capitalcite:<sha>"
log "Live /health on Azure is still UNVERIFIED until you curl the apiFqdn output."
