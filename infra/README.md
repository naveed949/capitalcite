# Azure infrastructure

IaC for issue #13. This directory is the source of truth for secrets, roles, and cost. The root README links here.

There is no live Azure apply in CI. `make deploy` and the Deploy Azure workflow skip with **UNVERIFIED** when `az` or OIDC secrets are missing. A skipped cloud step is never a pass.

## What it creates

Consumption Container Apps for the API (scale to zero) and Qdrant (internal ingress, default min replicas 1 so the index is not emptied). User-assigned identities, ACR Basic with the admin user off, Key Vault with RBAC, Log Analytics, and an Azure Files share for Qdrant storage.

Langfuse is not an Azure resource. Point `LANGFUSE_HOST` at Langfuse Cloud or leave the keys empty (ADR 0010: missing keys are a no-op).

## Offline checks

```bash
make infra-validate    # bicep format + build. docker build if Docker exists.
make deploy            # UNVERIFIED without az login. Does not pretend to pass.
```

## Opt-in apply

1. Create a resource group or let `scripts/deploy.sh` create `rg-capitalcite`.
2. `az login` locally, or create GitHub environment `azure` and federate the deploy identity (see Roles).
3. Store GitHub secrets `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID` (identity client id, tenant, subscription). No other secrets belong in GitHub.
4. Put `OPENAI_API_KEY` in Key Vault after the first apply if you want `ASK_MODEL=openai`. Pass `openaiSecretUri` on the next deploy.
5. Run **Actions → Deploy Azure** or `make deploy`.

The first apply uses the default API image (`mcr.microsoft.com/k8se/quickstart:latest`). That image is not capitalcite. Push the repo Dockerfile to ACR, then redeploy with `apiImage=<acr>.azurecr.io/capitalcite:<tag>`. Public `/health` on Azure stays **UNVERIFIED** until you curl `https://<apiFqdn>/health` yourself.

## Required GitHub secrets

| Secret | What it is |
| --- | --- |
| `AZURE_CLIENT_ID` | Client id of the user-assigned **deploy** identity |
| `AZURE_TENANT_ID` | Directory (tenant) id |
| `AZURE_SUBSCRIPTION_ID` | Subscription id |

OIDC subject: `repo:naveed949/capitalcite:environment:azure`. The Bicep federated credential uses that subject. Create the GitHub Environment named `azure` before the workflow can mint a token.

Chicken and egg: the first identity is created by a local `az deployment` (or a Cloud Shell apply) under your user. Then you copy `deployIdentityClientId` into GitHub. After that, the workflow can apply.

## Key Vault secrets (optional, not in Git)

| Secret name | App env var | Required to boot |
| --- | --- | --- |
| `openai-api-key` | `OPENAI_API_KEY` | No. Default `ASK_MODEL=fake` |
| `langfuse-public-key` | `LANGFUSE_PUBLIC_KEY` | No. Missing keys are a no-op |
| `langfuse-secret-key` | `LANGFUSE_SECRET_KEY` | No |

`SEC_USER_AGENT` is a Bicep parameter, not a vault secret. It must contain `@`.

## Roles

| Identity | Role | Scope |
| --- | --- | --- |
| Runtime (`ccite-rt…`) | AcrPull | ACR |
| Runtime | Key Vault Secrets User | Key Vault |
| Deploy (`ccite-deploy…`) | AcrPush | ACR |
| Deploy | Contributor | resource group |

The human who sets vault values needs **Key Vault Secrets Officer** (or a one-shot `az keyvault secret set` as subscription owner). The template does not put secret values in parameters.

## Estimated monthly cost (eastus list, 2026 public rates)

Numbers are estimates from published meters, not a bill. Treat them as **UNVERIFIED** against Azure Cost Management.

Consumption meters used here: active vCPU $0.000024/s, idle vCPU $0.000003/s, memory $0.000003/GiB-s. Monthly free grant per subscription: 180,000 vCPU-seconds, 360,000 GiB-seconds, 2 million requests.

### `infra/parameters/demo.json`

API min replicas 0, 0.25 vCPU, 0.5 Gi. Qdrant min replicas 1, same size.

| Resource | Idle month | Notes |
| --- | --- | --- |
| API Container App | ~$0 | Scaled to zero. Traffic bills consumption after the free grant. |
| Qdrant Container App, min 1, mostly idle | ~$4–8 | Idle vCPU 0.25 × $0.000003/s × 2.6e6 s ≈ $2; memory 0.5 Gi ≈ $4; minus free grant if this is the only app in the subscription. |
| ACR Basic | ~$5 | Billed even with no pushes. |
| Key Vault Standard | ~$0.03 / 10k operations | A handful of secret reads is cents. |
| Log Analytics, 30-day retention | ~$0–5 | Quiet demo stays near zero. |
| Storage Files 10 Gi, LRS | ~$2 | Qdrant volume quota. |
| Langfuse Cloud | $0 if omitted | Not deployed. |
| **Demo idle floor** | **~$11–20** | Before OpenAI tokens. |

A Qdrant replica that is actually indexing (active CPU) costs more: 0.25 active vCPU is about $16/month before the free grant.

### `infra/parameters/prod.json`

API min replicas 1, max 3. Same Qdrant.

| Resource | Idle month | Notes |
| --- | --- | --- |
| API always on, idle | ~$4–8 | Same idle math as Qdrant. |
| Qdrant min 1 | ~$4–8 | |
| ACR / Key Vault / Logs / Files | ~$7–12 | Same as demo. |
| **Prod-ish idle floor** | **~$15–28** | Plus OpenAI if `ASK_MODEL=openai`. |

OpenAI `gpt-4o-mini` is not an Azure meter. Leave `ASK_MODEL=fake` for a $0 model bill.

Deleting the resource group stops the Azure bill. Scale-to-zero does not stop ACR, Files, or a min-replica Qdrant.

## Why these services

See [docs/decisions/0012-azure-container-apps.md](../docs/decisions/0012-azure-container-apps.md).
