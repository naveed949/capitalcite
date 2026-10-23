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
app with internal ingress, why Langfuse stays off Azure, why Bicep is
the template language, and why identities plus every role assignment live
in a one-shot bootstrap template instead of the GitHub deploy.

## Container Apps, not App Service, not AKS

The issue named Container Apps. That matches the demo we actually have: one
HTTP process, scale to zero, no cluster to babysit.

App Service would run the same image. Linux Web Apps do not scale to zero
on the consumption story ACA has. A private Qdrant then needs a VNet, a
private endpoint, and another compute. We would be assembling the ACA
shape by hand.

AKS would be the right answer for a bank that already has a cluster,
network policies, and a platform team. It is the wrong answer for a
demo whose apply must skip without secrets. Node pools bill
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

Qdrant ingress is `external: false` and `allowInsecure: true`. Other apps
in the environment reach it at `http://<qdrant-app>:6333`. ACA internal
HTTP is not TLS. There is no public 6333. Compose on a laptop still binds
`127.0.0.1:6333`. That local path is unchanged.

Qdrant has no API key. Reach is environment-internal: the API talks to
the internal FQDN on the same Container Apps environment. An API key
would be a second secret to mint, store, and rotate for a hop that never
leaves the env. `QDRANT_PATH` must stay unset on ACA so the client uses
`QDRANT_URL` instead of a local disk.

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

CI and the deploy workflow pin Bicep **v0.48.1**. `releases/latest` would
let a compiler bump change compiled JSON without a review.

## Bootstrap vs main: Contributor cannot write role assignments

The deploy identity GitHub OIDC uses is **Contributor** on the resource
group. Contributor cannot create or update
`Microsoft.Authorization/roleAssignments`. That needs Owner or User
Access Administrator. A template that still contains those resources
fails `az deployment group create` with `AuthorizationFailed` even when
the assignments already exist. Putting identities, federated credentials,
and RBAC in `main.bicep` therefore broke "GH Action deploys from
scratch."

`infra/bootstrap.bicep` is the identity plane. A human with Owner / User
Access Administrator applies it once (`az deployment group create --name
bootstrap`). It creates the runtime identity, the deploy identity, GitHub
federated credentials (`environment:azure` and `ref:refs/heads/main`),
ACR, Key Vault, and **every** role assignment (AcrPull and Key Vault
Secrets User on the runtime identity; AcrPush and resource-group
Contributor on the deploy identity).

`infra/main.bicep` is the compute plane. It looks up the bootstrap
identities, ACR, and Key Vault by the same
`uniqueString(resourceGroup().id)` name formula. It creates Log
Analytics, Azure Files, the Container Apps environment, Qdrant, and the
API. It contains no role assignments. Compiled `main.json` must not
mention `Microsoft.Authorization/roleAssignments`.

Secret *values* are not parameters. Optional Key Vault URIs are. Empty
URIs omit the env vars so a first apply does not wait on secrets that do
not exist yet.

A leaked runtime identity cannot push images. ACR admin user is off. Key
Vault uses RBAC, not access policies. Storage is TLS 1.2 only and public
blob access is off.

## Public network access, private endpoints later

Key Vault, ACR, and storage set `publicNetworkAccess: 'Enabled'`. This
demo has no VNet, no private endpoints, and no private DNS. The GitHub
hosted runner needs a public ACR login to push. The human who sets vault
secrets needs a public Key Vault data plane. Private endpoints plus
disabled public access are the production step, once a VNet and a
self-hosted runner (or ACR Tasks) exist. Until then, RBAC and "admin
user off" are the controls.

## Image then Bicep, one write to the app

The default `apiImage` is `mcr.microsoft.com/k8se/quickstart:latest`.
That image is not capitalcite. Deploying `main.bicep` first, then
`az containerapp update --image`, left the live revision on a tag the
template did not declare. The next Bicep apply would revert to
quickstart.

The Deploy Azure workflow builds and pushes
`<acr>/capitalcite:${GITHUB_SHA}` first, then `az deployment group
create` of `main.bicep` with `apiImage` set to that tag. There is no
separate `az containerapp update --image`. The template is the only
writer of the running image.

## One deploy path, skip without secrets

`scripts/bootstrap.sh` what-ifs the identity plane. `scripts/deploy.sh`
is what `make deploy` runs: what-if of `main.bicep`, and UNVERIFIED if
the group or the `bootstrap` deployment is missing. The Deploy Azure
workflow is the image-then-Bicep apply, with a guard job that exits 0
and prints UNVERIFIED when the three OIDC secrets are empty. The guard
job does not use a GitHub Environment, so a missing `azure` environment
cannot block the skip path. The apply job is skipped, not failed. The
apply job does not create the resource group.

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
Settings contract. It does not attach private endpoints. Live `/health`
on Azure is UNVERIFIED until someone opts in, applies bootstrap as
Owner, applies main via Actions, and curls the output FQDN.

If issue #12 lands first and takes 0012, rebase this record to 0013.
