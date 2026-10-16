#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

log() {
  printf 'bootstrap: %s\n' "$*"
}

unverified() {
  log "UNVERIFIED: $*"
  exit 0
}

RG="${AZURE_RESOURCE_GROUP:-rg-capitalcite}"
LOCATION="${AZURE_LOCATION:-eastus}"
PARAMS="${BOOTSTRAP_PARAMS_FILE:-infra/parameters/bootstrap.json}"

if ! command -v az >/dev/null 2>&1; then
  unverified "az CLI is not installed. Apply infra/bootstrap.bicep from a shell with Owner / User Access Administrator."
fi

if ! az account show >/dev/null 2>&1; then
  unverified "az is not logged in. Run az login as Owner / User Access Administrator, then retry."
fi

if [[ ! -f "${PARAMS}" ]]; then
  log "missing parameters file ${PARAMS}" >&2
  exit 1
fi

if ! command -v bicep >/dev/null 2>&1 && ! az bicep version >/dev/null 2>&1; then
  unverified "bicep is not available to the logged-in az CLI."
fi

if ! az group show --name "${RG}" >/dev/null 2>&1; then
  log "resource group ${RG} is missing. Create it, then apply bootstrap as Owner:"
  log "  az group create --name ${RG} --location ${LOCATION}"
  log "  az deployment group create --name bootstrap --resource-group ${RG} --template-file infra/bootstrap.bicep --parameters ${PARAMS}"
  unverified "bootstrap was not applied. The GitHub deploy identity cannot create role assignments."
fi

log "what-if on ${RG} using ${PARAMS}"
az group what-if \
  --resource-group "${RG}" \
  --template-file infra/bootstrap.bicep \
  --parameters "${PARAMS}"
log "what-if finished. apply is a second, explicit command as Owner / User Access Administrator:"
log "  az deployment group create --name bootstrap --resource-group ${RG} --template-file infra/bootstrap.bicep --parameters ${PARAMS}"
