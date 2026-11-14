# Live demo

A web page that shows, second by second, how a KVM virtual machine and an LXC container react while
noisy neighbours are switched on one at a time.

![demo page on the host: default policy, n = 8](screenshots/05-default-n8.png)

*Real run on the nested Proxmox host (2026-11-14), `default` policy, 8 busy neighbours. All screenshots are
listed under [Screenshots from the host](#screenshots-from-the-host).*

## One-click start (Windows + VMware)

One-time setup, in the Proxmox shell (web console › Shell), as root:

```bash
git clone <this repository's URL> /root/os-noisy-neighbours   # skip if already cloned; any path works
bash /root/os-noisy-neighbours/demo/install-autostart.sh
```

This installs `open-vm-tools` and a systemd service (`os-demo`) that pulls the latest code and starts
`demo/demo.py` at every boot. After that, on Windows just double-click **`demo\start-demo.bat`**: it powers on
the VMware VM in the background (`vmrun … nogui`), asks VMware for the Proxmox IP, waits until the demo page
answers and opens it in the browser. No console login and no typing in the Proxmox shell.
`demo\stop-demo.bat` shuts Proxmox down cleanly.

The path of the Proxmox VM differs per computer: put the full path of its `.vmx` file on one line in
`demo\vmx-path.txt` (not committed), or pass `-Vmx <path>`. Without either, the default
`Documents\Virtual Machines\Ubuntun\Ubuntun.vmx` is used. If VMware cannot report the IP, put it on
one line in `demo\pve-ip.txt`.

**After the talk** turn auto-start off, otherwise every boot starts all guests and keeps the victims busy
(`osproj.sh all` refuses to run while the service is active):
`bash <repo>/demo/install-autostart.sh --remove`

## Run by hand

On the Proxmox host, as root, after `bash osproj.sh all` has built the guests:

```bash
python3 demo/demo.py            # prints http://<proxmox-ip>:8080/ — open it in a browser on the laptop
```

Without Proxmox (to rehearse the page and the talk): `DEMO_SIM=1 python3 demo/demo.py`, then http://localhost:8080/.

Standard library only. `Ctrl+C` stops the neighbour load and the benchmarks; the guests stay running.

## What is on the page

| Part | Meaning |
|---|---|
| **Busy neighbours** 0–8 | how many neighbour containers run a stress workload (keys `0`–`8`, `↑`/`↓`) |
| **Neighbours run** | CPU (`stress-ng --cpu`), memory bandwidth (`stress-ng --stream`) or disk (`fio` randrw) |
| **CPU placement policy** | `default` = Proxmox defaults · `float` = no pinning, CPU quota only · `dedicated` = each victim on its own two CPUs, neighbours on the rest (with 12 CPUs: container 10–11, VM 8–9, neighbours 0–7; the measurement in `osproj.sh` runs one victim at a time and uses 10–11 for both). Switching calls `osproj.sh policy <name>` and restarts the guests (about a minute) |
| Tiles | each victim's throughput and p95 latency relative to its own baseline, plus cgroup CPU pressure |
| Charts | the same three quantities over the last two minutes; vertical lines mark every change |
| CPU map | which CPUs each victim may use and how many busy neighbours share each CPU |

Both victims run `sysbench cpu --threads=2 --report-interval=1 --percentile=95` continuously. The baseline
is the median of the samples taken while no neighbour is busy; it is re-measured after a policy switch
or with **Re-measure baseline**.

## Demo flow

The step-by-step flow for the talk (timings, what to press, what the audience sees) is in the
[main README](../README.md#live-demo).

## Screenshots from the host

Taken on 2026-11-14 on the nested Proxmox host (Proxmox VE 9.2.2 inside VMware Workstation, so **not
report-grade**), CPU stress unless noted. Both victims run at the same time here, unlike `osproj.sh`, and the
page shows **p95** (the measurement harness reports p99). The tiles show one 1-second sample; shots 05, 08, 09
and 10 were retaken until that sample was close to the median of the last ~30 s (the first attempts caught
atypical seconds), the others were checked by eye against their charts, which show the full last 2 minutes.
In this boot Proxmox gave the container CPUs 1 and 2; neighbour 2 is allowed on CPU 2.

| file | state | VM (throughput, p95) | container (throughput, p95, CPU PSI) |
|---|---|---|---|
| [01-default-n0](screenshots/01-default-n0.png) | `default`, n = 0 | 101 %, ×1.0 | 100 %, ×1.0, 0 % |
| [02-default-n1](screenshots/02-default-n1.png) | `default`, n = 1 (no overlap with the container) | 100 %, ×1.0 | 100 %, ×1.0, 0 % |
| [03-default-n2](screenshots/03-default-n2.png) | `default`, n = 2 (neighbour 2 lands on CPU 2) | 97 %, ×1.1 | 49 %, ×12, 100 % |
| [04-default-n4](screenshots/04-default-n4.png) | `default`, n = 4 | 96 %, ×1.2 | 66 %, ×1.8, 57 % |
| [05-default-n8](screenshots/05-default-n8.png) | `default`, n = 8 | 95 %, ×1.1 | 56 %, ×11, 40 % |
| [06-default-n8-memory](screenshots/06-default-n8-memory.png) | `default`, n = 8, memory stress | 93 %, ×1.2 | 44 %, ×13, 53 % |
| [07-dedicated-n0](screenshots/07-dedicated-n0.png) | `dedicated`, n = 0 (VM on 8–9, container on 10–11) | 95 %, ×1.1 | 98 %, ×1.0, 0 % |
| [08-dedicated-n8](screenshots/08-dedicated-n8.png) | `dedicated`, n = 8 (neighbours on 0–7) | 91 %, ×1.7 | 86 %, ×1.8, 9 % |
| [09-float-n4](screenshots/09-float-n4.png) | `float`, n = 4 (left half of the chart: the earlier n = 8) | 92 %, ×1.7 | 89 %, ×1.7, 5 % |
| [10-float-n8](screenshots/10-float-n8.png) | `float`, n = 8 | 96 %, ×1.1 | 42 %, ×13, 48 % |

**For slides:** `screenshots/slides/` has each screenshot cut into `_tiles` (the two number cards),
`_charts` (the three time-series charts) and `_cpumap` (the CPU map), plus ready-made combinations:

| file | use |
|---|---|
| `A-effect_default-n2_tiles+throughput.png` | the effect: one neighbour on the container's CPU halves it, the VM does not move |
| `B-cause_cpumap_n1-vs-n2.png` | the cause: CPU map at n = 1 (no overlap) vs n = 2 (CPU 2 shared) |
| `C-policies-n8_tiles.png` | default / dedicated / float at n = 8, number cards stacked |
| `D1-timeline_default-n1-n2-n4_throughput.png` | throughput over time, n = 1 → 2 (collapse) → 4 |
| `D2-timeline_default-n4-n8_throughput.png` | throughput over time, n = 4 → 8 (left part: still n = 2) |

Regenerate with `python3 demo/crop_slides.py demo/screenshots demo/screenshots/slides` (needs Pillow).

What they show: under `default` the container collapses as soon as one busy neighbour shares one of its CPUs
(n = 2 here, not n = 1); with disjoint CPUs (`dedicated`) both guests end at about 0.9 with ~1.7–1.8× p95; with
no pinning (`float`) the container holds at n = 4 and collapses at n = 8 while the VM does not — the same
pattern as in the measured runs (`results/nested-v2-2026-11-13/REPORT.md`).

## Limits

- Only the project's guests are touched (VMIDs 9100, 9101, 9111–9118; names are checked at start).
- The page has no login. It accepts only the fixed choices above, but run it on a network you trust.
- Under `default`, which CPUs the container gets is decided by Proxmox at start, so step 2 may need more
  or fewer neighbours from one run to the next. Check the CPU map before the talk.
- Run end to end on the nested Proxmox host on 2026-11-14 (screenshots above); not yet on bare metal.
