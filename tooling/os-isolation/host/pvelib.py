"""Proxmox-side primitives used by validate.py, run_one.py and run_matrix.py.

Everything that touches qm / pct / ssh / sysfs lives here, behind one
class (`Platform`). Set EXP_SIMULATE=1 to get `SimPlatform` instead: it
runs the same orchestration on any Linux box with sysbench + fio, using
local processes as victim and neighbours. Use it to test the pipeline,
never to produce results.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from cfg import REPO, neighbour_ids

CGROOT = Path("/sys/fs/cgroup")
SSH_OPTS = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null", "-o", "LogLevel=ERROR",
            "-i", "/root/.ssh/id_ed25519"]
VCPU_COMM = re.compile(r"^CPU \d+/(KVM|TCG)$")


class CmdError(RuntimeError):
    pass


def sh(argv: list[str], timeout: float = 120, check: bool = True) -> str:
    """Run a command, return stdout. Raise CmdError with stderr on failure."""
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        raise CmdError(f"timeout after {timeout}s: {shlex.join(argv)}") from e
    if check and p.returncode != 0:
        raise CmdError(f"exit {p.returncode}: {shlex.join(argv)}\n{p.stderr.strip()[-800:]}")
    return p.stdout


def read_psi(path: Path) -> dict[str, float]:
    """Parse a PSI file -> {'some.avg10':.., 'some.total':.., 'full.avg10':.., ...}."""
    out: dict[str, float] = {}
    try:
        for line in path.read_text().splitlines():
            kind, *pairs = line.split()
            for pair in pairs:
                k, v = pair.split("=")
                out[f"{kind}.{k}"] = float(v)
    except (FileNotFoundError, PermissionError, ValueError):
        pass
    return out


def host_psi_avg10(resource: str) -> float:
    return read_psi(Path(f"/proc/pressure/{resource}")).get("some.avg10", 0.0)


def last_json_line(text: str) -> dict:
    for line in reversed(text.strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            return json.loads(line)
    raise CmdError(f"no JSON line in benchmark output:\n{text[-800:]}")


class Platform:
    """Real Proxmox VE host."""
    simulated = False

    def __init__(self, cfg: dict):
        self.c = cfg
        self.vmid = int(cfg["ids"]["vm"])
        self.ctid = int(cfg["ids"]["ct"])
        self.vm_ip = cfg["host"]["vm_ip"]
        self.nbrs = neighbour_ids(cfg)

    # ---------------------------------------------------------- lifecycle
    def _vm_running(self) -> bool:
        return "running" in sh(["qm", "status", str(self.vmid)], check=False)

    def _ct_running(self, ctid: int) -> bool:
        return "running" in sh(["pct", "status", str(ctid)], check=False)

    def ensure_victims_running(self, arms=("vm", "ct")) -> None:
        """Start the victims of the given arms if needed (both stay up; the idle one is quiet)."""
        if "vm" in arms:
            if not self._vm_running():
                sh(["qm", "start", str(self.vmid)], timeout=120)
            deadline = time.time() + 180
            while True:
                try:
                    self.victim_exec("vm", ["true"], timeout=10)
                    break
                except CmdError:
                    if time.time() > deadline:
                        raise CmdError(f"VM {self.vmid} not reachable over SSH at {self.vm_ip}")
                    time.sleep(3)
        if "ct" in arms and not self._ct_running(self.ctid):
            sh(["pct", "start", str(self.ctid)], timeout=120)

    def ensure_neighbours_running(self) -> None:
        for i in self.nbrs:
            if not self._ct_running(i):
                sh(["pct", "start", str(i)], timeout=120)

    # ------------------------------------------------------------ victims
    def victim_exec(self, arm: str, argv: list[str], timeout: float = 300) -> str:
        if arm == "vm":
            return sh(["ssh", *SSH_OPTS, f"ubuntu@{self.vm_ip}", "sudo", *argv], timeout=timeout)
        if arm == "ct":
            return sh(["pct", "exec", str(self.ctid), "--", *argv], timeout=timeout)
        raise ValueError(arm)

    def vm_pid(self) -> int | None:
        try:
            return int(Path(f"/var/run/qemu-server/{self.vmid}.pid").read_text().split()[0])
        except (FileNotFoundError, ValueError, IndexError):
            return None

    @staticmethod
    def cgroup_of_pid(pid: int) -> Path | None:
        try:
            for line in Path(f"/proc/{pid}/cgroup").read_text().splitlines():
                if line.startswith("0::"):
                    return CGROOT / line[3:].lstrip("/")
        except FileNotFoundError:
            pass
        return None

    @staticmethod
    def ct_cgroup(ctid: int) -> Path | None:
        for cand in (CGROOT / "lxc" / str(ctid), CGROOT / f"lxc.payload.{ctid}"):
            if (cand / "cpu.stat").exists():
                return cand
        return None

    def victim_cgroup(self, arm: str) -> Path | None:
        if arm == "vm":
            pid = self.vm_pid()
            cg = self.cgroup_of_pid(pid) if pid else None
            return cg or (CGROOT / "qemu.slice" / f"{self.vmid}.scope")
        return self.ct_cgroup(self.ctid)

    def nbr_cgroup(self, ctid: int) -> Path | None:
        return self.ct_cgroup(ctid)

    def vcpu_tids(self) -> list[int]:
        pid = self.vm_pid()
        if not pid:
            return []
        tids = []
        for task in Path(f"/proc/{pid}/task").glob("*"):
            try:
                if VCPU_COMM.match((task / "comm").read_text().strip()):
                    tids.append(int(task.name))
            except FileNotFoundError:
                continue
        return tids

    def guest_steal(self) -> int | None:
        """Steal ticks reported inside the VM (/proc/stat, 8th field)."""
        try:
            first = self.victim_exec("vm", ["head", "-n1", "/proc/stat"], timeout=15).split()
            return int(first[8])
        except (CmdError, IndexError, ValueError):
            return None

    def drop_caches(self, arm: str = "vm") -> None:
        """Host page cache always; the VM's own page cache too when the VM is the victim."""
        sh(["sh", "-c", "sync; echo 3 > /proc/sys/vm/drop_caches"])
        if arm != "vm":
            return
        try:
            self.victim_exec("vm", ["sh", "-c", "sync; echo 3 > /proc/sys/vm/drop_caches"], timeout=60)
        except CmdError:
            pass  # VM not reachable: the run will fail later with a clearer error

    # --------------------------------------------------------- neighbours
    def _nbr_cmd(self, dim: str, intensity: dict, seconds: int) -> str:
        cores = int(self.c["neighbour"]["cores"])
        if dim == "cpu":
            cmd = (f"stress-ng --cpu {cores} --cpu-load {int(intensity['cpu_load'])} "
                   f"--timeout {seconds}s --quiet")
        elif dim == "io":
            mib = int(float(self.c["neighbour"]["io_file_gb"]) * 1024)
            cmd = (f"fio --name=nbr --filename=/root/nbr.dat --size={mib}M --rw=randwrite --bs=4k "
                   f"--iodepth={int(intensity['io_iodepth'])} --ioengine=libaio --direct=1 "
                   f"--time_based --runtime={seconds} --output=/dev/null")
        else:
            raise ValueError(dim)
        return f"setsid -f {cmd} >/dev/null 2>&1 </dev/null"

    def _parallel(self, ids: list[int], fn) -> list:
        if not ids:
            return []
        with ThreadPoolExecutor(max_workers=len(ids)) as ex:
            return list(ex.map(fn, ids))

    def start_load(self, ids: list[int], dim: str, intensity: dict, seconds: int) -> None:
        cmd = self._nbr_cmd(dim, intensity, seconds)
        self._parallel(ids, lambda i: sh(["pct", "exec", str(i), "--", "sh", "-c", cmd], timeout=30))

    def stop_load(self, ids: list[int]) -> None:
        self._parallel(ids, lambda i: sh(["pct", "exec", str(i), "--", "sh", "-c",
                                          "pkill stress-ng; pkill -x fio; true"], timeout=30, check=False))

    def count_load(self, ids: list[int]) -> int:
        probe = "echo $(( $(pgrep -c stress-ng || true) + $(pgrep -cx fio || true) ))"
        counts = self._parallel(ids, lambda i: sh(["pct", "exec", str(i), "--", "sh", "-c", probe],
                                                  timeout=30, check=False))
        return sum(1 for c in counts if c.strip().isdigit() and int(c) > 0)

    def close(self) -> None:
        pass


def get_platform(cfg: dict):
    if os.environ.get("EXP_SIMULATE") == "1":
        from simulate import SimPlatform
        return SimPlatform(cfg)
    return Platform(cfg)


def check_arms(verdict: str, arms, allow_nested: bool = True) -> None:
    """Refuse arms the platform cannot run. BLOCKED_NO_KVM still allows a CT-only pipeline test."""
    if verdict.startswith("BLOCKED") and "vm" in arms:
        raise SystemExit(f"platform verdict {verdict}: no KVM, the VM arm cannot run here (README §Platform).\n"
                         "A CT-only pipeline test is still possible: pass --arm ct / --arms ct.")
    if verdict.startswith("NESTED") and not allow_nested:
        raise SystemExit(f"platform verdict {verdict}: results will carry a nested-virtualisation confound.\n"
                         "Re-run with --allow-nested if you accept that (README §Platform).")
    if verdict.startswith("BLOCKED"):
        print(f"WARNING: platform {verdict} — CT-only pipeline test, not a result for the report", flush=True)


def load_platform_info() -> dict:
    p = REPO / "docs" / "platform.json"
    if os.environ.get("EXP_SIMULATE") == "1":
        cpus = os.cpu_count() or 1
        return {"verdict": "SIMULATED", "cpus": cpus, "nbr_cores": max(1, int(cpus / 4 + 0.5))}
    if not p.exists():
        raise SystemExit("docs/platform.json missing — run host/preflight.sh first")
    return json.loads(p.read_text())
