# API image measurement

One run of `scripts/measure_image.sh`. The lines in Raw lines are copied from `image-measure.raw.txt`. This page does not round them.

## Commands

`make image IMAGE=capitalcite:local` builds the image when the script runs.

```text
docker run -d --memory 512m --memory-swap 512m -p 127.0.0.1::8000 -e SEC_USER_AGENT='capitalcite-measure you@example.com' -e ASK_MODEL=fake capitalcite:local
```

`--memory-swap 512m` is set equal to `--memory` so the cgroup cannot add swap past 512m. On this machine the script uses `sudo docker` when `docker info` is denied.

`nproc` and `uptime` are in the raw log. Stats samples are `image-measure.stats.txt`. Container logs are `image-measure.container.log`. The `/health` body is `image-measure.health.json`. The first `/ask/stream` body is `image-measure.ask1.body`.

## Raw lines

```text
nproc=4
uptime= 17:19:37 up 42 min,  0 user,  load average: 0.11, 0.15, 0.09
Size=230276144
host_port=32769
cold_start_ns=2276761650
cold_start_s=2.27676165
health_json={"status":"ok","service":"capitalcite","version":"0.1.0","git_sha":"deee14b2e0354578c3c4d855cdb850084d3c3d1e","point_count":12}
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
OOMKilled=false Status=running ExitCode=0 Error=
disk 67287419	/app/fastembed
disk 94877	/app/qdrant
disk 392773593	/app/.venv
VmPeak:	 1404124 kB
VmHWM:	  387832 kB
VmRSS:	  387832 kB
Rss:              387832 kB
Pss:              387072 kB
peak_stats_parsed_bytes=333866598
peak_stats_line=318.4MiB / 512MiB 62.19% cpu=173.64%
vmrss_kb_samples=2
vmrss_kb_max=387824
```
