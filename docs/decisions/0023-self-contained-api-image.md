# 0023. Self-contained API image

Date: 2026-10-23

Status: Accepted

The API image answers from a fixture index and a FastEmbed cache that the
build writes. Boot does not call EDGAR and does not download a model.
`GET /health` stays HTTP 200 and adds `git_sha` plus a best-effort
`point_count`. Browser origins are fail-closed. `POST /ask` and
`POST /ask/stream` share one in-process limit per TCP peer.

## Bake the index and the models

A fetch at boot would need Hugging Face for `BAAI/bge-small-en-v1.5` and
`Qdrant/bm25`, and it would need EDGAR if the index were not already in the
image. The demo image has no `.git` and no network requirement at start.
The builder copies `tests/fixtures/chunking/corpus`, runs
`capitalcite chunk` and `capitalcite index --embedder bge-small`, and
writes a pinned FastEmbed cache so `FASTEMBED_CACHE_PATH=/app/fastembed`
is populated. `prefetch_fastembed_cache` calls
`huggingface_hub.snapshot_download` with
`Qdrant/bge-small-en-v1.5-onnx-Q@aa8f8b060edb00e03bfdd08813a2949946c8ba55`
and `Qdrant/bm25@22b8d2af71a76161e18dd432d2cee0eefa66e412`, then checks
the sha256 of `model_optimized.onnx`, `tokenizer.json`, and `english.txt`.
The runtime stage copies that cache and `/app/qdrant`, sets
`HF_HUB_OFFLINE=1`, and sets `GIT_SHA` from the build arg. `make image`
passes `git rev-parse HEAD`. Base images are pinned by digest.

FastEmbed's offline check looks for `mock.file` and `tamil.txt` next to the
`Qdrant/bm25` snapshot. That snapshot does not ship those names. The
prefetch step adds empty files. English stopwords still come from
`english.txt`.

The lifespan opens one `RetrievalSetup` and stores it on `app.state`.
`get_setup` and `/health` reuse that object. Shutdown closes it. A
per-request client on an embedded `QDRANT_PATH` fails under concurrent
`/ask` because the folder lock is exclusive. `/health` stays 200. A
missing store or a count error is `point_count` 0.

## What one memory run showed

`scripts/measure_image.sh` is the run. One execution on 2026-10-23, after
the offline cache fix. `nproc` was 4. `uptime` was
`17:19:37 up 42 min, 0 user, load average: 0.11, 0.15, 0.09`.
The container command was:

```text
docker run -d --memory 512m --memory-swap 512m -p 127.0.0.1::8000 \
  -e SEC_USER_AGENT='capitalcite-measure you@example.com' \
  -e ASK_MODEL=fake capitalcite:local
```

`--memory-swap 512m` matches `--memory` so the cgroup cannot add swap past
512 MiB. This host needed `sudo docker`.

Copied from `docs/deploy/image-measure.raw.txt`:

```text
Size=230276144
cold_start_ns=2276761650
cold_start_s=2.27676165
health_json={"status":"ok","service":"capitalcite","version":"0.1.0","git_sha":"deee14b2e0354578c3c4d855cdb850084d3c3d1e","point_count":12}
ask_http_200=20 ask_not_200=0
OOMKilled=false Status=running ExitCode=0 Error=
disk 67287419	/app/fastembed
disk 94877	/app/qdrant
disk 392773593	/app/.venv
VmPeak:	 1404124 kB
VmHWM:	  387832 kB
VmRSS:	  387832 kB
peak_stats_line=318.4MiB / 512MiB 62.19% cpu=173.64%
vmrss_kb_samples=2
vmrss_kb_max=387824
```

`cold_start_s` is container start until the first `/health` 200. That
handler does not load FastEmbed. The 20 `POST /ask/stream` calls are the
work that loads the model. All 20 returned HTTP 200 and the first body is
`text/event-stream`. The script did not record each request's duration.

`VmHWM` is the kernel high-water resident set for pid 1, `387832 kB`.
End `VmRSS` is the same value, so the 20 requests did not raise it further.
`docker stats` produced two samples. The higher sample is
`318.4MiB / 512MiB`. That display is lower than `VmHWM` because the
sampler and the cgroup counter are not the same reading. `OOMKilled=false`.
The 512 MiB cap was not what stopped the process.

A later look, not the 20-request run, mapped `/proc/1/smaps` after one
`POST /ask/stream`. `docs/deploy/image-measure.smaps.txt` records
`rss_kb_total 387772`. The largest buckets are anonymous `262836` kB and
`[heap]` `51308` kB. The largest named file is
`onnxruntime_pybind11_state` at `20268` kB. On disk the FastEmbed cache is
`67287419` bytes and the Qdrant directory is `94877` bytes. The resident
set is mostly anonymous memory after ONNX loads, not the Qdrant files.
This record does not split that anonymous region any further. Halving the
resident set would mean a smaller loaded runtime. This run did not try a
smaller model.

`docker image inspect` `Size` is `230276144` bytes. `du -sb /app/.venv`
inside the container is `392773593` bytes. Those commands measure different
things. The record keeps both.

## One process, one counter

`AskLimiter` keeps an `OrderedDict` of deques of monotonic timestamps and
one `asyncio.Lock`. `ASK_RATE_LIMIT=0` does not count. The image sets `60`.
Local Settings stay at `0`. `TRUSTED_PROXY_HOPS` defaults to 0, so the
address is the TCP peer. When it is N>0, the key is the X-Forwarded-For
entry N positions from the right. The leftmost entry is never the key. A
short or empty header falls back to the peer. Set the variable to the
number of trusted reverse proxies in front of the app. The map evicts the
oldest key once it holds 10_000 addresses. Expired timestamps on the
current key drop on each hit. A short sweep of the oldest keys runs every
32 hits. A second replica has its own map. The cap is not a global quota.

The dependency sits on the ask router, so it runs before the handler.
A 429 is JSON with `Retry-After`, including `POST /ask/stream`.
`GET /health` is not on that router.

## Closed CORS

`CORS_ORIGINS` is a comma-separated tuple. Blank entries are dropped.
An empty tuple adds no `CORSMiddleware`. A request `Origin` is echoed only
when it is in the tuple. `*` is rejected at Settings load so the list
cannot become an open allow-all. `allow_credentials` is false. The local
example file leaves the variable empty.

The image sets `QDRANT_PATH=/app/qdrant`. A process that should use a
remote store must blank that path. `infra/main.bicep` sets `QDRANT_PATH`
to an empty string so `QDRANT_URL` wins.

## Alternatives

Fetching the model or the filings at container start keeps the image
smaller and makes boot depend on Hugging Face and EDGAR. This record bakes
both so start does not.

A shared store for the limiter would count across replicas. The demo image
is one process. The record says the map does not cross a process boundary
instead of adding another service.

Trusting the leftmost `X-Forwarded-For` entry would let a caller pick a
fresh key. hops=0 keeps the peer. hops=N uses only the hop the trusted
proxy appended.

Per-request Qdrant clients on one embedded path serialize behind a folder
lock and fail under overlap. One lifespan client is the owner of that
path.

Resolving FastEmbed `revision/main` at build time would move when the hub
moves. The builder pins the revisions CI already resolved and checks the
file hashes.

A sidecar Qdrant would keep the vector store out of the API image. The
baked directory is `94877` bytes. A second container is not worth that.

A `/ready` route that fails when `point_count` is 0 would turn liveness
into readiness. verify.sh still needs `/health` 200 before it builds an
index. The count stays on `/health` and a failure to read it is 0.

A separate module for image paths and another for HTTP policy would split
fields that are already environment settings. `Settings` parses them once.
The handler, the middleware, and the limiter read those fields.
