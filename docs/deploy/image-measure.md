# API image measurement

One run of `scripts/measure_image.sh`. The lines in Raw lines are copied from `image-measure.raw.txt`. This page does not round them.

## Commands

`make image IMAGE=capitalcite:local` builds the image when the script runs.

```text
docker run -d --memory 512m --memory-swap 512m -p 127.0.0.1::8000 -e SEC_USER_AGENT='capitalcite-measure you@example.com' -e ASK_MODEL=fake capitalcite:local
```

`--memory-swap 512m` is set equal to `--memory` so the cgroup cannot add swap past 512m. On this machine the script uses `sudo -n docker` when `docker info` is denied.

`nproc` and `uptime` are in the raw log. This host reported `nproc=4`. Timings came from that 4-CPU machine and will be slower on a fractional-CPU instance. `cold_start_s` is container start until the first `/health` 200, before FastEmbed loads. `first_answer_s` is that same start until the first `POST /ask/stream` finishes. `inspect_size_bytes` is the stored image size from `docker image inspect .Size`. `save_bytes` is the compressed `docker save | wc -c` stream. `unpacked_root_bytes` is the uncompressed root from `du -sb /` inside the running container. Stats samples are `image-measure.stats.txt`. Container logs are `image-measure.container.log`. The `/health` body is `image-measure.health.json`. The first `/ask/stream` body is `image-measure.ask1.body`.

## Raw lines

```text
nproc=4
uptime= 18:27:09 up  1:49,  0 user,  load average: 0.22, 0.09, 0.17
Size=230421567
inspect_size_bytes=230421567
save_bytes=230440960
host_port=32775
cold_start_ns=2747372050
cold_start_s=2.74737205
health_json={"status":"ok","service":"capitalcite","version":"0.1.0","git_sha":"2c16f43ffa3ce1da601cfa75945910cb9b853689","point_count":12}
first_answer_http=200
first_answer_ns=3181537367
first_answer_s=3.181537367
ask 1 http=200 bytes=9076
ask 2 http=200 bytes=9076
ask 3 http=200 bytes=9076
ask 4 http=200 bytes=9076
ask 5 http=200 bytes=9076
ask 6 http=200 bytes=9076
ask 7 http=200 bytes=9076
ask 8 http=200 bytes=9076
ask 9 http=200 bytes=9076
ask 10 http=200 bytes=9076
ask 11 http=200 bytes=9076
ask 12 http=200 bytes=9076
ask 13 http=200 bytes=9076
ask 14 http=200 bytes=9076
ask 15 http=200 bytes=9076
ask 16 http=200 bytes=9076
ask 17 http=200 bytes=9076
ask 18 http=200 bytes=9076
ask 19 http=200 bytes=9076
ask 20 http=200 bytes=9076
ask_http_200=20 ask_not_200=0 codes= 200 200 200 200 200 200 200 200 200 200 200 200 200 200 200 200 200 200 200 200
concurrent_kinds=ask stream health ask stream health
concurrent_http_200=6 concurrent_not_200=0 concurrent_codes=200 200 200 200 200 200
OOMKilled=false Status=running ExitCode=0 Error=
disk 67578666	/app/fastembed
disk 94877	/app/qdrant
disk 392788540	/app/.venv
disk 185214286	/
unpacked_root_bytes=645676306
VmPeak:	 1371692 kB
VmHWM:	  363536 kB
VmRSS:	  363536 kB
Rss:              363536 kB
Pss:              362782 kB
peak_stats_parsed_bytes=311112499
peak_stats_line=296.7MiB / 512MiB 57.96% cpu=57.99%
vmrss_kb_samples=2
vmrss_kb_max=362296
```
