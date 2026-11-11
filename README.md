# Noisy Neighbours on Proxmox VE: Performance Isolation of KVM VMs and LXC Containers

Operating Systems term project, National Chung Cheng University, Fall 2026 (two-person team).

We repeat the *competing-workload interference* experiment of Sharma et al. (Middleware 2016) on a current
Proxmox VE stack (kernel 7.0, cgroup v2): one measured guest (a KVM VM or an LXC container) runs next to
n noisy-neighbour LXC containers, and we measure how much its CPU, memory and disk performance degrade.
Slowdowns are explained with host-side kernel counters (cgroup v2 PSI) instead of only being reported.

> **Status (2026-11-11): pilot data only.** All results in this repository come from Proxmox VE running
> *inside VMware Workstation* (nested virtualisation). They are useful for the method and for CPU
> placement effects, but the VM arm carries an extra virtualisation layer and disk results are dominated
> by the VMware virtual disk. Report-grade data needs a bare-metal Proxmox host (planned).

## Repository layout

```
osproj.sh                      experiment harness used for all results here (bash, runs on the Proxmox host)
results/nested-2026-10-19/     raw.csv (long format), summary.csv, placement.tsv (CPU placement log), meta.txt (platform + guest configs)
analysis/                      load.py + plot.py -> fig1_cpu.png, fig2_mem_io.png, fig3_psi.png
demo/                          live demo: web page with neighbour / stress / policy controls (see demo/README.md)
tooling/os-isolation/          alternative Python harness (randomised blocks, 1 Hz sampler, report generator);
                               tested in simulation only, not used for the results above
```

## Experiment (results/nested-2026-10-19)

| Factor | Levels |
|---|---|
| Measured guest | KVM VM (Debian 13 cloud image, 2 vCPU, 2 GB, `cache=none,iothread=1`) · LXC CT (Debian, 2 cores, 2 GB, unprivileged) |
| Neighbours | n = 0, 2, 4, 8 LXC containers (2 cores, 1 GB each) |
| Neighbour stress | CPU (`stress-ng --cpu`), memory bandwidth (`stress-ng --stream`), disk (`fio` 4k randrw QD32 direct) |
| CPU placement policy | `default` (Proxmox defaults) · `float` (`cpulimit`, no cpuset) · `dedicated` (victim and neighbours on disjoint cpusets) |
| Victim benchmarks (15 s each) | `sysbench cpu` (events/s, p99), `sysbench memory` (MiB/s), `fio` 4k randread QD16 direct (IOPS, p99), 1 ms sleep wake-up latency |
| Repetitions | 3, policy order rotated per repetition, stress configurations shuffled, baseline before and drift check after each block |

Host: Proxmox VE 9.2.2 on 12 vCPUs / 16 GB, nested in VMware Workstation 17 on an Intel i5-14400 (Windows 11).

Run it (on the Proxmox host, as root): `bash osproj.sh all` · `bash osproj.sh status` · `bash osproj.sh summary` ·
`bash osproj.sh policy <default|float|dedicated>` ·
`bash osproj.sh clean` (destroys only the project's own guests, VMIDs 9100, 9101, 9111–9118, after checking their names).

Live demo: `python3 demo/demo.py` on the Proxmox host, or `DEMO_SIM=1 python3 demo/demo.py` anywhere ([details](demo/README.md)).

Reproduce the figures: `pip install pandas numpy scipy matplotlib && python3 analysis/plot.py`

## Pilot findings (nested platform, 3 repetitions)

![CPU contention](analysis/fig1_cpu.png)

1. **CPU: the container degrades only when its CPUs overlap with busy neighbours.** Under Proxmox defaults
   the container's cpuset overlapped with active neighbours in most runs: CPU throughput fell to 0.44–0.59 of
   baseline and p99 latency rose 13–17×, while the VM stayed at 0.85–0.90. With disjoint cpusets
   (`dedicated`) the two curves coincide.
2. **Open question: VM advantage from the cgroup hierarchy?** With no pinning (`float`) the container still
   collapses at n = 8 (0.55, p99 13×) while the VM does not (0.86). Hypothesis: the VM sits alone under
   `qemu.slice`, while the measured container shares `lxc/` with all eight neighbours. Not yet verified.
3. **A ~15 % loss no placement policy removes** (also with disjoint CPUs), invisible to PSI: likely frequency
   scaling, SMT sharing or host scheduling of the nested vCPUs.
4. **Memory bandwidth and disk are not isolated for either guest type.** Under disk stress both converge to
   the same absolute performance (~7.2k IOPS, p99 ~5.9 ms at n = 8); the larger *relative* drop of the
   container comes from its higher baseline, not from worse isolation.
5. **Attribution:** victim-cgroup CPU PSI tracks the container's CPU slowdown (Spearman ρ = −0.85); I/O PSI
   is not comparable across guest types (asynchronous libaio waits are not counted for the container).

![Memory and disk](analysis/fig2_mem_io.png)
![PSI vs slowdown](analysis/fig3_psi.png)

## Remaining work

- Bare-metal Proxmox run (5 repetitions), n = 1 added, p95 recorded, so that the density n* can be interpolated.
- Record `cpu.stat` throttling, `io.stat` and VM steal time per run.
- Verify the cgroup-hierarchy hypothesis (`cpu.weight` of `qemu.slice` vs `lxc`, plus a control run).
- Comparison table with Sharma et al. (2016); final report.

## References

1. W. Felter, A. Ferreira, R. Rajamony, J. Rubio. *An Updated Performance Comparison of Virtual Machines and Linux Containers.* IEEE ISPASS 2015.
2. P. Sharma, L. Chaufournier, P. Shenoy, Y. C. Tay. *Containers and Virtual Machines at Scale: A Comparative Study.* Middleware 2016.
3. Linux kernel documentation, *Control Group v2* and *PSI — Pressure Stall Information*.
4. Proxmox VE Administration Guide.
