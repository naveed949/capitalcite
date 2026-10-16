# 0012. Azure Container Apps, Bicep, no live apply in CI

Date: 2026-10-16

Status: Accepted

Issue #13 asks for a multi-stage Dockerfile, a managed vector store or a
Qdrant container, secrets in Key Vault or ACA secrets, and IaC (Bicep or
Terraform). The public `/health` must work after a from-scratch deploy.
Naveed's constraint on this change: infrastructure as code only. No Azure
credentials, no spend, no live deploy. A skipped cloud step is UNVERIFIED,
never passed.

This record is why the compute is Container Apps, why Qdrant is a second
app with internal ingress, why Langfuse stays off Azure, and why Bicep is
the template language.

## Container Apps, not App Service, not AKS

The issue named Container Apps. That matches the demo we actually have: one
HTTP process, scale to zero, no cluster to babysit.

App Service would run the same image. Linux Web Apps do not scale to zero
on the consumption story ACA has. A private Qdrant then needs a VNet, a
private endpoint, and another compute. We would be assembling the ACA
shape by hand.

AKS would be the right answer for a bank that already has a cluster,
network policies, and a platform team. It is the wrong answer for a
portfolio demo whose apply must skip without secrets. Node pools bill
whether the API is idle or not.

The API app sets `minReplicas` 0 and an HTTP scale rule. Ingress is
external and `allowInsecure` is false. TLS terminates at the environment.
The container probe still uses HTTP on port 8000 because that is what
uvicorn listens on.

## Qdrant as a Container App, not Azure AI Search

`open_qdrant_client` already dials `QDRANT_URL` with an optional API key,
or opens a local path, or `:memory:`. Hybrid retrieval stores dense and
BM25 vectors in one Qdrant collection (ADR 0005). Azure AI Search is a
different API. Swapping it would rewrite retrieval, which is not this
issue.

A Qdrant sidecar on the API app would share the API's scale-to-zero
policy. Scale to zero would drop the in-memory index unless the volume
survives, and the first request would pay both cold starts. Two apps let
the API sleep and keep Qdrant at `minReplicas` 1 with an Azure Files
share at `/qdrant/storage`.

Qdrant ingress is `external: false`. Other apps in the environment reach
it at `http://<qdrant-app>:6333`. There is no public 6333. Compose on a
laptop still binds `127.0.0.1:6333`. That local path is unchanged.

Qdrant Cloud would also work. It is another vendor and another secret.
An ACA app keeps the vector store inside the same identity and network
boundary as the API.

## Langfuse is not hosted here

ADR 0010 already made tracing a no-op without keys. Self-host Langfuse is
web, worker, Postgres, ClickHouse, Redis, and MinIO. Putting that on ACA
would dominate cost and blast radius. The compose stack stays for
laptops. Azure gets `LANGFUSE_HOST` pointing at Langfuse Cloud, or empty
keys.

## Bicep, not Terraform

The cloud is Azure only. Bicep compiles with the standalone CLI and no
login (`bicep build`). Terraform would need a provider init and a state
backend. Those are real for a multi-cloud shop and extra for this repo.

The domain model is the resource graph in `infra/main.bicep`. Parameters
files are the two tiers (`demo.json`, `prod.json`). Secret *values* are
not parameters. Optional Key Vault URIs are. Empty URIs omit the env vars
so a first apply does not wait on secrets that do not exist yet.

Two identities: a runtime identity the apps use (AcrPull, Key Vault
Secrets User) and a deploy identity GitHub OIDC uses (AcrPush, Contributor
on the group). A leaked runtime identity cannot push images. Federated
credentials cover `environment:azure` and `ref:refs/heads/main`.

ACR admin user is off. Key Vault uses RBAC, not access policies. Storage
is TLS 1.2 only and public blob access is off.

## One deploy path, skip without secrets

`scripts/deploy.sh` is what `make deploy` runs. The Deploy Azure workflow
is the same apply, with a guard job that exits 0 and prints UNVERIFIED
when the three OIDC secrets are empty. The guard job does not use a
GitHub Environment, so a missing `azure` environment cannot block the
skip path. The apply job is skipped, not failed.

CI never calls `az login`. `.github/workflows/infra.yml` runs `bicep
build`, `docker build`, and gitleaks. That workflow does not touch
`.github/workflows/ci.yml`, so issue #12's in-flight PR can keep adding
`capitalcite check` without a merge fight on the Python job.

The image default `ASK_MODEL=fake` so the process boots without
`OPENAI_API_KEY`. Settings already ignore unknown Azure env vars
(`extra="ignore"`). `/health` still does not construct Qdrant.

## What this issue does not do

It does not apply the template. It does not curl a public Azure FQDN. It
does not put Langfuse or Neo4j on Azure. It does not change the Python
Settings contract. Live `/health` on Azure is UNVERIFIED until someone
opts in, applies, and curls the output FQDN.

If issue #12 lands first and takes 0012, rebase this record to 0013.
