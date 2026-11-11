#!/usr/bin/env python3
"""Host-side sampler (INF-06).

Reads cumulative kernel counters once per interval and keeps them as
long-format rows: (run_id, t, target, metric, value). Analysis turns
cumulative counters into rates by differencing the first and last sample.

Per cgroup target (victim, nbr1..):
  cpu.usage_usec cpu.user_usec cpu.system_usec cpu.nr_periods cpu.nr_throttled cpu.throttled_usec
  cpu.pressure.{some,full}.total  io.pressure.{some,full}.total  memory.pressure.{some,full}.total
  io.rbytes io.wbytes io.rios io.wios  memory.current
Host:
  host.{cpu,io,memory}.pressure.{some,full}.total
  host.stat.{busy,total,steal}_jiffies
VM only (target "victim"):
  vcpu.run_ns vcpu.run_delay_ns vcpu.timeslices vcpu.threads   (from /proc/<qemu>/task/<vcpu>/schedstat)

Standalone use (debugging / demo):
  python3 host/sampler.py --arm vm --seconds 30           # print per-second rates
"""
from __future__ import annotations

import argparse
import threading
import time
from pathlib import Path

CPU_STAT_KEYS = ("usage_usec", "user_usec", "system_usec", "nr_periods", "nr_throttled", "throttled_usec")
IO_KEYS = ("rbytes", "wbytes", "rios", "wios")


def _read_kv(path: Path) -> dict[str, int]:
    out = {}
    try:
        for line in path.read_text().splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[1].lstrip("-").isdigit():
                out[parts[0]] = int(parts[1])
    except (FileNotFoundError, PermissionError):
        pass
    return out


def _read_psi_totals(path: Path) -> dict[str, int]:
    out = {}
    try:
        for line in path.read_text().splitlines():
            kind, *pairs = line.split()
            for pair in pairs:
                k, v = pair.split("=")
                if k == "total":
                    out[f"{kind}.total"] = int(v)
    except (FileNotFoundError, PermissionError, ValueError):
        pass
    return out


def _read_iostat(path: Path) -> dict[str, int]:
    tot = {k: 0 for k in IO_KEYS}
    found = False
    try:
        for line in path.read_text().splitlines():
            for pair in line.split()[1:]:
                k, _, v = pair.partition("=")
                if k in tot and v.isdigit():
                    tot[k] += int(v)
                    found = True
    except (FileNotFoundError, PermissionError):
        pass
    return tot if found else {}


def read_cgroup(cg: Path) -> dict[str, int]:
    m: dict[str, int] = {}
    st = _read_kv(cg / "cpu.stat")
    for k in CPU_STAT_KEYS:
        if k in st:
            m[f"cpu.{k}"] = st[k]
    for res in ("cpu", "io", "memory"):
        for k, v in _read_psi_totals(cg / f"{res}.pressure").items():
            m[f"{res}.pressure.{k}"] = v
    for k, v in _read_iostat(cg / "io.stat").items():
        m[f"io.{k}"] = v
    try:
        m["memory.current"] = int((cg / "memory.current").read_text().strip())
    except (FileNotFoundError, ValueError):
        pass
    return m


def read_host() -> dict[str, int]:
    m: dict[str, int] = {}
    for res in ("cpu", "io", "memory"):
        for k, v in _read_psi_totals(Path(f"/proc/pressure/{res}")).items():
            m[f"host.{res}.pressure.{k}"] = v
    try:
        f = [int(x) for x in Path("/proc/stat").read_text().splitlines()[0].split()[1:]]
        # user nice system idle iowait irq softirq steal [guest guest_nice — already in user]
        idle = f[3] + f[4]
        total = sum(f[:8])
        m["host.stat.total_jiffies"] = total
        m["host.stat.busy_jiffies"] = total - idle
        m["host.stat.steal_jiffies"] = f[7]
    except (FileNotFoundError, IndexError, ValueError):
        pass
    return m


def read_vcpus(pid: int, tids: list[int]) -> dict[str, int]:
    run = delay = slices = n = 0
    for tid in tids:
        try:
            a, b, c = Path(f"/proc/{pid}/task/{tid}/schedstat").read_text().split()[:3]
            run, delay, slices, n = run + int(a), delay + int(b), slices + int(c), n + 1
        except (FileNotFoundError, ValueError):
            continue
    if not n:
        return {}
    return {"vcpu.run_ns": run, "vcpu.run_delay_ns": delay, "vcpu.timeslices": slices, "vcpu.threads": n}


class Sampler(threading.Thread):
    """Background sampler. targets: {name: cgroup_dir}. vcpu: (qemu_pid, [tids]) or None."""

    def __init__(self, run_id: str, targets: dict[str, Path], vcpu=None, interval: float = 1.0):
        super().__init__(daemon=True)
        self.run_id, self.targets, self.vcpu, self.interval = run_id, targets, vcpu, interval
        self.rows: list[tuple] = []
        self._halt = threading.Event()
        self._t0 = time.monotonic()

    def sample_once(self) -> None:
        t = round(time.monotonic() - self._t0, 3)
        for name, cg in self.targets.items():
            vals = read_cgroup(cg) if cg else {}
            if name == "victim" and self.vcpu:
                vals.update(read_vcpus(*self.vcpu))
            for k, v in vals.items():
                self.rows.append((self.run_id, t, name, k, v))
        for k, v in read_host().items():
            self.rows.append((self.run_id, t, "host", k, v))

    def run(self) -> None:
        nxt = time.monotonic()
        while not self._halt.is_set():
            self.sample_once()
            nxt += self.interval
            self._halt.wait(max(0.0, nxt - time.monotonic()))
        self.sample_once()          # closing sample at stop time

    def stop(self) -> list[tuple]:
        self._halt.set()
        self.join(timeout=10)
        return self.rows


# ------------------------------------------------------------------ CLI
def _cli() -> None:
    import cfg as cfgmod
    from pvelib import get_platform

    ap = argparse.ArgumentParser(description="print per-second victim pressure and vCPU wait")
    ap.add_argument("--arm", choices=["vm", "ct"], required=True)
    ap.add_argument("--seconds", type=int, default=30)
    a = ap.parse_args()
    plat = get_platform(cfgmod.load())
    cg = plat.victim_cgroup(a.arm)
    vcpu = (plat.vm_pid(), plat.vcpu_tids()) if a.arm == "vm" else None
    print(f"victim cgroup: {cg}   vCPU threads: {vcpu[1] if vcpu else '-'}")
    prev, prev_t = None, None
    for _ in range(a.seconds + 1):
        cur = read_cgroup(cg) if cg else {}
        if vcpu:
            cur.update(read_vcpus(*vcpu))
        now = time.monotonic()
        if prev is not None:
            dt_us = (now - prev_t) * 1e6
            def rate(k, scale=1.0):
                return (cur.get(k, 0) - prev.get(k, 0)) / dt_us * 100 * scale if k in cur else float("nan")
            print(f"cpu used {rate('cpu.usage_usec'):6.1f}%  cpu.pressure some {rate('cpu.pressure.some.total'):5.1f}%  "
                  f"io.pressure some {rate('io.pressure.some.total'):5.1f}%  "
                  f"vCPU wait {rate('vcpu.run_delay_ns', 1e-3):6.1f}%", flush=True)
        prev, prev_t = cur, now
        time.sleep(1)


if __name__ == "__main__":
    _cli()
