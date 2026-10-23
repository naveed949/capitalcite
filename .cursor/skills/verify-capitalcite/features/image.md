# Demo image

`make image` builds a CPU image that answers from a fixture index and a FastEmbed cache baked at build time. The running container does not call EDGAR. `capitalcite --help` works inside the image. `GET /health` returns 200 with a non-empty `git_sha` and a `point_count` of at least 1.

## Sub-features

- `image-help` runs `capitalcite --help` with the image entrypoint and exits 0. The help text lists `ingest`.
- `image-health` runs the image with `SEC_USER_AGENT` set and reads `GET /health`. Status is 200. `git_sha` is a non-empty string. `point_count` is an integer of at least 1.
- `image-absent` writes `image.skipped` when docker cannot run. That result is UNVERIFIED.

## How to get to it (user POV)

- From the repo root, run `make image`. The tag defaults to `capitalcite:local`. `GIT_SHA` defaults to `git rev-parse HEAD`.
- Run the image with `SEC_USER_AGENT` set to a name and an email. `ASK_MODEL` is already `fake`.
- `./scripts/verify.sh` builds `capitalcite:verify` when docker works, then runs help and `/health`.
- `./scripts/measure_image.sh` records cold start, image size, and memory under `--memory 512m`. That script is separate from the pass/fail helper.

## Driving it with make and curl

Preconditions:

- Docker can run `docker info`, or `sudo -n docker info` when the user is not in the docker group.
- If neither works, write `image.skipped` with that reason and do not fail the rest of `./scripts/verify.sh`.
- Do not copy `.env` into the image. Pass `SEC_USER_AGENT` at `docker run`.

- **Build.** Run `make image IMAGE=capitalcite:verify`. Exit code is 0.
- **CLI help.** Run `docker run --rm --entrypoint capitalcite capitalcite:verify --help`. Exit code is 0. stdout lists `ingest`.
- **Health in the image.** Run `docker run -d -e SEC_USER_AGENT='capitalcite-verify you@example.com' -p 127.0.0.1:${IMAGE_PORT}:8000 capitalcite:verify`. Curl `http://127.0.0.1:${IMAGE_PORT}/health` until HTTP 200. The JSON `git_sha` is non-empty. `point_count` is at least 1. Stop that container.
- **Bundled helper.** Run `./scripts/verify.sh`. When docker works it writes `image.help.out`, `image.help.exit`, and `image.health.body`. Otherwise it writes `image.skipped`.
- **Proof.** Keep the help output and the health body, or the skip file. A skipped image is UNVERIFIED. Pytest is not this feature.

## Gotchas

- The image sets `ASK_RATE_LIMIT=60`. Local Settings stay at 0 unless the environment sets the variable.
- `HF_HUB_OFFLINE=1` is set after prefetch so index and the model-load check fail instead of fetching `revision/main`. The builder pins `Qdrant/bge-small-en-v1.5-onnx-Q` and `Qdrant/bm25` by revision and checks the ONNX, tokenizer, and config hashes.
- `EMBED_THREADS` is 1 in the image so a 0.1 CPU host is not oversubscribed.
- `scripts/measure_image.sh` records sequential asks and a mixed concurrent `/ask`, `/ask/stream`, and `/health` burst under `--memory 512m`. `inspect_size_bytes` is the stored image size from `docker image inspect`. `save_bytes` is the compressed `docker save` stream. `unpacked_root_bytes` is the uncompressed `du -sb /`. Timings came from a 4-CPU host and will be slower on a fractional-CPU instance. `cold_start_s` is first `/health` 200. `first_answer_s` is the first `/ask/stream`. An OOM is recorded. It is not rewritten into a success.
- `QDRANT_PATH` in the image is `/app/qdrant`. That directory is owned by uid 65532.
- Without docker, `make image` prints `UNVERIFIED` and exits 0.
