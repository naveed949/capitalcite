# API image measurement

One run of `scripts/measure_image.sh`. The lines in Raw lines are copied from `image-measure.raw.txt`. This page does not round them.

## Commands

`make image IMAGE=capitalcite:local` builds the image when the script runs.

```text
docker run -d --memory 512m --memory-swap 512m -p 127.0.0.1::8000 -e SEC_USER_AGENT='capitalcite-measure you@example.com' -e ASK_MODEL=fake capitalcite:local
```

`--memory-swap 512m` is set equal to `--memory` so the cgroup cannot add swap past 512m. On this machine the script uses `sudo -n docker` when `docker info` is denied.

`nproc` and `uptime` are in the raw log. This host reported `nproc=4`. `cold_start_s` is container start until the first `/health` 200, before FastEmbed loads. `first_answer_s` is that same start until the first `POST /ask/stream` finishes. `inspect_size_bytes` is `docker image inspect .Size` (uncompressed layers). `save_bytes` is `docker save | wc -c`. `unpacked_root_bytes` is `du -sb /` inside the running container. Stats samples are `image-measure.stats.txt`. Container logs are `image-measure.container.log`. The `/health` body is `image-measure.health.json`. The first `/ask/stream` body is `image-measure.ask1.body`.

## Raw lines

```text
nproc=4
uptime= 17:54:34 up  1:17,  0 user,  load average: 0.16, 0.15, 0.17
Size=230414418
inspect_size_bytes=230414418
save_bytes=230433792
host_port=32774
cold_start_ns=2699822690
cold_start_s=2.69982269
health_json={"status":"ok","service":"capitalcite","version":"0.1.0","git_sha":"1b6fcff1481f576f191cc2ed8a62602caecc5fd8","point_count":12}
first_answer_http=200
first_answer_ns=3145223679
first_answer_s=3.145223679
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
concurrent_http_200=6 concurrent_not_200=0 concurrent_codes=200 200 200 200 200 200
OOMKilled=false Status=running ExitCode=0 Error=
disk 67583907	/app/fastembed
disk 94877	/app/qdrant
disk 392786588	/app/.venv
disk 185215456	/
unpacked_root_bytes=645680765
VmPeak:	 1445380 kB
VmHWM:	  362692 kB
VmRSS:	  362692 kB
Rss:              362692 kB
Pss:              361910 kB
peak_stats_parsed_bytes=311322214
peak_stats_line=296.9MiB / 512MiB 57.99% cpu=58.98%
vmrss_kb_samples=2
vmrss_kb_max=362120
```
