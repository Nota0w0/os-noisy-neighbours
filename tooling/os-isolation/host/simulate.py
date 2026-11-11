"""SimPlatform — test the orchestration and analysis pipeline without Proxmox.

Enabled with EXP_SIMULATE=1. Needs sysbench and fio on the local machine.
  * the "victim" is the real guest/bench_*.sh run locally
  * "neighbours" are local sysbench / fio processes, so contention is real
  * cgroup files are fakes in kernel format, updated from the real host
    PSI and /proc/stat, so the sampler and analysis code paths are exercised
Results produced this way are labelled platform=SIMULATED and must never
be reported.
"""
from __future__ import annotations

import os
import subprocess
import threading
import time
from pathlib import Path

from cfg import REPO, neighbour_ids
from pvelib import CmdError, read_psi


class FakeKernel(threading.Thread):
    def __init__(self, root: Path, names: list[str], ncpu: int):
        super().__init__(daemon=True)
        self.root, self.ncpu = root, ncpu
        self.c = {n: dict(usage=0, cpu_some=0, cpu_full=0, io_some=0, io_full=0, rbytes=0, wbytes=0) for n in names}
        self.active: set[str] = set()
        self._halt = threading.Event()
        self._prev = self._host()
        for n in names:
            (root / n).mkdir(parents=True, exist_ok=True)
            self._write(n)

    @staticmethod
    def _host():
        cpu = read_psi(Path("/proc/pressure/cpu"))
        io = read_psi(Path("/proc/pressure/io"))
        f = [int(x) for x in Path("/proc/stat").read_text().splitlines()[0].split()[1:9]]
        return dict(t=time.monotonic(), cs=cpu.get("some.total", 0), cf=cpu.get("full.total", 0),
                    ios=io.get("some.total", 0), iof=io.get("full.total", 0),
                    busy=sum(f) - f[3] - f[4], total=sum(f))

    def _write(self, n: str) -> None:
        c, d = self.c[n], self.root / n
        files = {
            "cpu.stat": (f"usage_usec {c['usage']}\nuser_usec {c['usage']}\nsystem_usec 0\n"
                         "nr_periods 0\nnr_throttled 0\nthrottled_usec 0\n"),
            "cpu.pressure": (f"some avg10=0.00 avg60=0.00 avg300=0.00 total={c['cpu_some']}\n"
                             f"full avg10=0.00 avg60=0.00 avg300=0.00 total={c['cpu_full']}\n"),
            "io.pressure": (f"some avg10=0.00 avg60=0.00 avg300=0.00 total={c['io_some']}\n"
                            f"full avg10=0.00 avg60=0.00 avg300=0.00 total={c['io_full']}\n"),
            "io.stat": f"8:0 rbytes={c['rbytes']} wbytes={c['wbytes']} rios=0 wios=0 dbytes=0 dios=0\n",
            "memory.current": "104857600\n",
        }
        for fname, text in files.items():
            tmp = d / (fname + ".tmp")
            tmp.write_text(text)
            os.replace(tmp, d / fname)

    def run(self) -> None:
        while not self._halt.wait(0.25):
            cur = self._host()
            p = self._prev
            dt_us = (cur["t"] - p["t"]) * 1e6
            busy = (cur["busy"] - p["busy"]) / max(1, cur["total"] - p["total"])
            for n in self.c:
                c = self.c[n]
                c["cpu_some"] += int(cur["cs"] - p["cs"])
                c["cpu_full"] += int(cur["cf"] - p["cf"])
                c["io_some"] += int(cur["ios"] - p["ios"])
                c["io_full"] += int(cur["iof"] - p["iof"])
                if n in self.active:
                    c["usage"] += int(dt_us * min(2, busy * self.ncpu))
                    c["rbytes"] += int(dt_us * 50)
                self._write(n)
            self._prev = cur

    def stop(self):
        self._halt.set()
        self.join(timeout=5)


class SimPlatform:
    simulated = True

    def __init__(self, cfg: dict):
        self.c = cfg
        self.nbrs = neighbour_ids(cfg)
        self.dir = Path(os.environ.get("EXP_SIM_DIR", REPO / "cache" / "sim"))
        self.dir.mkdir(parents=True, exist_ok=True)
        names = ["victim-vm", "victim-ct"] + [f"nbr{i}" for i in self.nbrs]
        self.kernel = FakeKernel(self.dir / "cg", names, os.cpu_count() or 1)
        self.kernel.start()
        self.procs: dict[int, subprocess.Popen] = {}
        self.vm_ip = "simulated"

    # lifecycle: nothing to start
    def ensure_victims_running(self, arms=("vm", "ct")): pass
    def ensure_neighbours_running(self): pass
    def drop_caches(self, arm="vm"): pass
    def vm_pid(self): return None
    def vcpu_tids(self): return []
    def guest_steal(self): return None

    def victim_cgroup(self, arm: str) -> Path:
        return self.dir / "cg" / f"victim-{arm}"

    def nbr_cgroup(self, ctid: int) -> Path:
        return self.dir / "cg" / f"nbr{ctid}"

    def victim_exec(self, arm: str, argv: list[str], timeout: float = 300) -> str:
        if argv and argv[0].startswith("/opt/bench/"):
            script = REPO / "guest" / Path(argv[0]).name
            env = dict(os.environ, BENCH_IO_FILE=str(self.dir / "victim.dat"))
            if script.name == "bench_io.sh" and not (self.dir / "victim.dat").exists():
                subprocess.run([str(script), "prep", "0.25"], env=env, check=True, capture_output=True)
            if script.name == "bench_io.sh" and len(argv) >= 5:
                argv = list(argv)
                argv[4] = "0.25"            # keep the simulated file small
            self.kernel.active.add(f"victim-{arm}")
            try:
                p = subprocess.run([str(script), *argv[1:]], env=env, capture_output=True,
                                   text=True, timeout=timeout)
            finally:
                self.kernel.active.discard(f"victim-{arm}")
            if p.returncode:
                raise CmdError(p.stderr[-800:])
            return p.stdout
        return ""                           # true, drop caches, /proc/stat …

    def start_load(self, ids, dim, intensity, seconds):
        for i in ids:
            if dim == "cpu":
                cmd = ["sysbench", "cpu", "--threads=1", f"--time={seconds}", "run"]
            else:
                cmd = ["fio", "--name=nbr", f"--filename={self.dir / f'nbr{i}.dat'}", "--size=64M",
                       "--rw=randwrite", "--bs=4k", f"--iodepth={int(intensity['io_iodepth'])}",
                       "--ioengine=libaio", "--direct=1", "--time_based", f"--runtime={seconds}",
                       "--output=/dev/null"]
            self.procs[i] = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.kernel.active.add(f"nbr{i}")

    def stop_load(self, ids):
        for i in ids:
            p = self.procs.pop(i, None)
            if p and p.poll() is None:
                p.terminate()
                try:
                    p.wait(5)
                except subprocess.TimeoutExpired:
                    p.kill()
            self.kernel.active.discard(f"nbr{i}")

    def count_load(self, ids) -> int:
        return sum(1 for i in ids if i in self.procs and self.procs[i].poll() is None)

    def close(self):
        self.stop_load(list(self.procs))
        self.kernel.stop()
