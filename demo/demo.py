#!/usr/bin/env python3
"""Live demo: start noisy neighbours one by one and watch the VM and the container diverge.

Run on the Proxmox host as root, after `bash osproj.sh all` has built the guests:

    python3 demo/demo.py                 # then open http://<proxmox-ip>:8080 in a browser
    python3 demo/demo.py --port 9000
    DEMO_SIM=1 python3 demo/demo.py      # no Proxmox needed: fake data, to rehearse the UI

What it does
  * keeps BOTH victims (victim-vm 9100, victim-ct 9101) running `sysbench cpu` and reads one
    sample per second from each (throughput and p95 latency);
  * reads each victim's cgroup cpu.pressure and everybody's cpuset from the host;
  * the web page has buttons for: number of busy neighbours (0-8), what they run
    (CPU / memory / disk stress), and the CPU placement policy (default / float / dedicated).

Only the project's own guests are touched (names are checked first). Standard library only.
The page has no login: it only accepts the fixed choices above, but bind it to a network you trust.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import shlex
import signal
import statistics
import subprocess
import sys
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from queue import Queue

HERE = Path(__file__).resolve().parent
OSPROJ = HERE.parent / "osproj.sh"
W = Path(os.environ.get("W", "/root/osproj"))
KEY = W / "id_ed25519"
CG = Path("/sys/fs/cgroup")
VMID_VM, VMID_CT = 9100, 9101
NBR_IDS = [9110 + k for k in range(1, 9)]
VICT_CORES, NBR_CORES = 2, 2
STRESSES = ("cpu", "mem", "io")
POLICIES = ("default", "float", "dedicated")
STRESS_SECONDS = 1800                      # neighbours stop by themselves if the demo dies
SYSBENCH = (f"stdbuf -oL sysbench cpu --threads={VICT_CORES} --time=3600 "
            "--report-interval=1 --percentile=95 run")
LINE = re.compile(r"\[\s*\d+s\s*\].*?eps:\s*([\d.]+)\s+lat \(ms,\s*95%\):\s*([\d.]+)")


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def sh(argv, timeout=60, check=True) -> str:
    p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    if check and p.returncode:
        raise RuntimeError(f"{' '.join(map(str, argv))}: {p.stderr.strip()[-300:]}")
    return p.stdout


def cpulist(text: str) -> list[int]:
    out: list[int] = []
    for part in text.strip().split(","):
        if not part:
            continue
        a, _, b = part.partition("-")
        out.extend(range(int(a), int(b or a) + 1))
    return sorted(set(out))


# ====================================================================== real Proxmox backend
class VictimLoop(threading.Thread):
    """Runs sysbench inside one victim and keeps the newest (time, events/s, p95 ms)."""

    def __init__(self, name: str, argv: list[str], kill_argv: list[str]):
        super().__init__(daemon=True)
        self.name_, self.argv, self.kill_argv = name, argv, kill_argv
        self.latest: tuple[float, float, float] | None = None
        self.halt = threading.Event()
        self.proc: subprocess.Popen | None = None

    def run(self) -> None:
        while not self.halt.is_set():
            subprocess.run(self.kill_argv, capture_output=True)          # no stale sysbench
            self.proc = subprocess.Popen(self.argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                         stdin=subprocess.DEVNULL, text=True)
            for line in self.proc.stdout:
                m = LINE.search(line)
                if m:
                    self.latest = (time.time(), float(m.group(1)), float(m.group(2)))
                if self.halt.is_set():
                    break
            self.proc.wait()
            if not self.halt.is_set():
                log(f"{self.name_}: sysbench ended, restarting in 2 s")
                self.halt.wait(2)

    def stop(self) -> None:
        self.halt.set()
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
        subprocess.run(self.kill_argv, capture_output=True)
        self.latest = None


class PveBackend:
    simulated = False

    def __init__(self):
        self.loops: dict[str, VictimLoop] = {}
        self.noise: dict[int, subprocess.Popen] = {}
        self._psi_prev: dict[str, tuple[float, int]] = {}
        ip = sh(["ip", "-4", "-br", "addr", "show", "vmbr0"]).split()[2].split("/")[0]
        self.pve_ip = ip
        self.vm_ip = ip.rsplit(".", 1)[0] + ".60"
        self.ssh = ["ssh", "-n", "-i", str(KEY), "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
                    "-o", "LogLevel=ERROR", "-o", "ConnectTimeout=5", "-o", "BatchMode=yes", f"debian@{self.vm_ip}"]
        self.ncpu = os.cpu_count() or 1

    # ---- safety: refuse to touch anything that is not ours
    def guard(self) -> None:
        if "name: victim-vm" not in sh(["qm", "config", str(VMID_VM)]):
            raise SystemExit(f"VMID {VMID_VM} is not victim-vm — run `bash osproj.sh all` first")
        want = {VMID_CT: "victim-ct", **{i: f"nbr{k}" for k, i in enumerate(NBR_IDS, 1)}}
        for i, name in want.items():
            if f"hostname: {name}\n" not in sh(["pct", "config", str(i)]):
                raise SystemExit(f"CT {i} is not {name} — run `bash osproj.sh all` first")
        if sh(["pgrep", "-f", "osproj[.]sh _all"], check=False).strip():
            raise SystemExit("a measurement run (osproj.sh all) is in progress — not starting the demo")

    def _vexec(self, v: str, cmd: str) -> list[str]:
        if v == "vm":
            return [*self.ssh, f"sudo bash -c {shlex.quote(cmd)}"]
        return ["pct", "exec", str(VMID_CT), "--", "bash", "-c", cmd]

    def start_guests(self) -> None:
        for i in [*NBR_IDS, VMID_CT]:
            if "running" not in sh(["pct", "status", str(i)], check=False):
                log(f"starting CT {i}")
                sh(["pct", "start", str(i)], timeout=120)
        if "running" not in sh(["qm", "status", str(VMID_VM)], check=False):
            log("starting victim-vm")
            sh(["qm", "start", str(VMID_VM)], timeout=120)
        deadline = time.time() + 240
        while subprocess.run([*self.ssh, "true"], capture_output=True).returncode:
            if time.time() > deadline:
                raise RuntimeError(f"victim-vm not reachable over SSH at {self.vm_ip}")
            time.sleep(3)
        for v in ("vm", "ct"):
            sh(self._vexec(v, "command -v sysbench stdbuf >/dev/null"), timeout=30)

    def start_loops(self) -> None:
        for v in ("vm", "ct"):
            loop = VictimLoop(v, self._vexec(v, SYSBENCH), self._vexec(v, "pkill -x sysbench; true"))
            self.loops[v] = loop
            loop.start()

    def stop_loops(self) -> None:
        for loop in self.loops.values():
            loop.stop()
        self.loops = {}

    def prepare(self) -> None:
        self.guard()
        self.clear_noise()
        self.start_guests()
        self.start_loops()

    # ---- measurements
    def latest(self, v: str):
        loop = self.loops.get(v)
        return loop.latest if loop else None

    def _vm_pid(self):
        try:
            return int(Path(f"/var/run/qemu-server/{VMID_VM}.pid").read_text().split()[0])
        except (OSError, ValueError, IndexError):
            return None

    def _cgroup(self, v: str) -> Path | None:
        if v == "ct":
            return CG / "lxc" / str(VMID_CT)
        pid = self._vm_pid()
        try:
            for line in Path(f"/proc/{pid}/cgroup").read_text().splitlines():
                if line.startswith("0::"):
                    return CG / line[3:].lstrip("/")
        except OSError:
            pass
        return None

    def psi_pct(self, v: str):
        cg = self._cgroup(v)
        try:
            total = int(re.search(r"^some .*total=(\d+)", (cg / "cpu.pressure").read_text(), re.M).group(1))
        except (OSError, AttributeError, TypeError):
            return None
        now, prev = time.time(), self._psi_prev.get(v)
        self._psi_prev[v] = (now, total)
        if not prev or now <= prev[0] or total < prev[1]:
            return None
        return min(100.0, (total - prev[1]) / ((now - prev[0]) * 1e6) * 100)

    @staticmethod
    def _ct_cpus(ctid: int) -> list[int]:
        for cand in (CG / "lxc" / str(ctid) / "ns" / "cpuset.cpus.effective",
                     CG / "lxc" / str(ctid) / "cpuset.cpus.effective"):
            try:
                return cpulist(cand.read_text())
            except OSError:
                continue
        return []

    def placement(self) -> dict:
        vm: list[int] = []
        try:
            status = Path(f"/proc/{self._vm_pid()}/status").read_text()
            vm = cpulist(re.search(r"Cpus_allowed_list:\s*(\S+)", status).group(1))
        except (OSError, AttributeError):
            pass
        return {"ncpu": self.ncpu, "vm": vm, "ct": self._ct_cpus(VMID_CT),
                "nbr": [self._ct_cpus(i) for i in NBR_IDS]}

    def policy(self) -> str:
        conf = Path(f"/etc/pve/lxc/{VMID_CT}.conf").read_text()
        if "lxc.cgroup2.cpuset.cpus" in conf:
            return "dedicated"
        return "float" if re.search(r"^cpulimit:", conf, re.M) and not re.search(r"^cores:", conf, re.M) else "default"

    # ---- actions
    @staticmethod
    def _stress_cmd(kind: str) -> str:
        return {"cpu": f"stress-ng --cpu {NBR_CORES} --cpu-method matrixprod --timeout {STRESS_SECONDS}s",
                "mem": f"stress-ng --stream {NBR_CORES} --timeout {STRESS_SECONDS}s",
                "io": ("fio --name=n --filename=/root/n.dat --size=1G --rw=randrw --bs=4k --iodepth=32 "
                       f"--ioengine=libaio --direct=1 --runtime={STRESS_SECONDS} --time_based")}[kind]

    def _stop_nbr(self, k: int) -> None:
        subprocess.run(["pct", "exec", str(NBR_IDS[k - 1]), "--", "bash", "-c", "pkill -x stress-ng; pkill -x fio; true"],
                       capture_output=True, timeout=30)
        p = self.noise.pop(k, None)
        if p:
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()

    def clear_noise(self) -> None:
        threads = [threading.Thread(target=self._stop_nbr, args=(k,)) for k in range(1, 9)]
        [t.start() for t in threads]
        [t.join() for t in threads]

    def apply_neighbours(self, n: int, stress: str, restart: bool) -> None:
        if restart:
            self.clear_noise()
        for k in sorted(self.noise):
            if k > n:
                self._stop_nbr(k)
        for k in range(1, n + 1):
            if k not in self.noise or self.noise[k].poll() is not None:
                self.noise[k] = subprocess.Popen(
                    ["pct", "exec", str(NBR_IDS[k - 1]), "--", "bash", "-c", self._stress_cmd(stress)],
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def _dedicated_env(self) -> dict[str, str]:
        """CPU sets for `dedicated`. osproj.sh measures one victim at a time and gives both the same
        two CPUs; here both run at once, so each victim gets its own pair and the neighbours the rest."""
        n, c = self.ncpu, VICT_CORES
        if n < 2 * c + 2:                                   # too few CPUs to split: keep osproj.sh defaults
            return {}
        return {"DED_CT": f"{n - c}-{n - 1}", "DED_VM": f"{n - 2 * c}-{n - c - 1}", "SHARED": f"0-{n - 2 * c - 1}"}

    def apply_policy(self, pol: str) -> None:
        self.stop_loops()
        self.clear_noise()
        out = subprocess.run(["bash", str(OSPROJ), "policy", pol], capture_output=True, text=True, timeout=600,
                             env={**os.environ, **self._dedicated_env()})
        if out.returncode:
            raise RuntimeError(f"osproj.sh policy {pol} failed: {(out.stdout + out.stderr).strip()[-300:]}")
        self._psi_prev = {}
        self.start_guests()
        self.start_loops()

    def shutdown(self) -> None:
        self.stop_loops()
        self.clear_noise()


# ====================================================================== simulation backend
class SimBackend:
    """Fake numbers shaped like the pilot results, to rehearse the page without Proxmox."""
    simulated = True

    def __init__(self):
        self.ncpu, self.pol, self.n, self.stress = 12, "default", 0, "cpu"
        self.rng = random.Random(7)
        self._place()

    def _place(self) -> None:
        r, allc = self.rng, list(range(self.ncpu))
        if self.pol == "default":
            self.ct, self.vm = sorted(r.sample(allc, 2)), allc
            self.nbr = [sorted(r.sample(allc, 2)) for _ in range(8)]
        elif self.pol == "float":
            self.ct, self.vm, self.nbr = allc, allc, [allc] * 8
        else:
            self.ct, self.vm, self.nbr = allc[-2:], allc[-4:-2], [allc[:-4]] * 8   # as PveBackend._dedicated_env

    def prepare(self) -> None:
        pass

    def _overlap(self) -> int:
        busy = {c for cp in self.nbr[: self.n] for c in cp}
        return len(busy & set(self.ct))

    def _rel(self, v: str) -> tuple[float, float, float]:
        """-> (throughput ratio, p95 ratio, cpu pressure %)"""
        n = self.n
        if n == 0 or self.stress == "io":
            return 1.0, 1.0, 0.1
        common = {0: 1.0, 1: 0.93, 2: 0.89, 3: 0.88}.get(n, 0.86 if n < 8 else 0.845)
        lat = 1.07 if n < 4 else 1.7
        if v == "ct":
            ov = self._overlap()
            if self.pol == "default" and ov:
                return (0.55 if ov == 1 else 0.42), 14.0, (95.0 if ov == 1 else 52.0)
            if self.pol == "float" and n >= 6:
                return 0.55 + 0.05 * (8 - n), 13.0, 30.0
        return common, lat, 0.4

    def latest(self, v: str):
        thr, lat, _ = self._rel(v)
        g = self.rng.gauss
        return time.time(), 7700 * thr * (1 + g(0, 0.012)), max(0.28, 0.32 * lat * (1 + g(0, 0.04)))

    def psi_pct(self, v: str):
        return max(0.0, self._rel(v)[2] * (1 + self.rng.gauss(0, 0.08)))

    def placement(self) -> dict:
        return {"ncpu": self.ncpu, "vm": self.vm, "ct": self.ct, "nbr": self.nbr}

    def policy(self) -> str:
        return self.pol

    def apply_neighbours(self, n: int, stress: str, restart: bool) -> None:
        time.sleep(0.4)
        self.n, self.stress = n, stress

    def apply_policy(self, pol: str) -> None:
        self.n = 0
        time.sleep(5)
        self.pol = pol
        self._place()

    def shutdown(self) -> None:
        pass


# ====================================================================== shared state + sampler
class Demo:
    def __init__(self, backend):
        self.b = backend
        self.lock = threading.Lock()
        self.samples: deque = deque(maxlen=1800)
        self.events: deque = deque(maxlen=200)
        self.seq = 0
        self.n, self.stress = 0, "cpu"
        self.policy = backend.policy()
        self.busy: str | None = "Starting guests and benchmarks…"
        self.error: str | None = None
        self.base: dict[str, dict] = {}
        self._base_buf: dict[str, deque] = {"vm": deque(maxlen=30), "ct": deque(maxlen=30)}
        self._settle_until = time.time() + 5
        self.place: dict = {}
        self.actions: Queue = Queue()
        self.halt = threading.Event()

    # ---- background threads
    def worker(self) -> None:
        try:
            self.b.prepare()
            self.policy = self.b.policy()
            self._event("ready")
        except BaseException as e:                          # noqa: BLE001 — shown on the page
            self.error = f"startup failed: {e}"
            log(self.error)
        self.busy = None
        while not self.halt.is_set():
            kind, arg = self.actions.get()
            try:
                if kind == "neighbours":
                    n, stress = arg
                    self.busy = None
                    self.b.apply_neighbours(n, stress, restart=(stress != self.stress))
                    with self.lock:
                        self.n, self.stress = n, stress
                        self._settle_until = time.time() + 4
                    self._event(f"n = {n}" + (f" · {stress}" if n else ""))
                elif kind == "policy":
                    self.busy = f"Switching CPU placement to “{arg}” — guests are restarting (about a minute)…"
                    self.b.apply_policy(arg)
                    with self.lock:
                        self.n, self.policy = 0, self.b.policy()
                        self._reset_baseline()
                    self._event(f"policy: {self.policy}")
                elif kind == "rebaseline":
                    with self.lock:
                        self._reset_baseline()
                self.error = None
            except Exception as e:                           # noqa: BLE001
                self.error = f"{kind} failed: {e}"
                log(self.error)
            self.busy = None

    def _reset_baseline(self) -> None:
        self.base = {}
        for buf in self._base_buf.values():
            buf.clear()
        self._settle_until = time.time() + 4

    def _event(self, label: str) -> None:
        with self.lock:
            self.events.append({"t": time.time(), "label": label})
        log(label)

    def sampler(self) -> None:
        tick = 0
        while not self.halt.wait(1.0):
            now = time.time()
            row = {"t": round(now, 2), "n": self.n}
            for v in ("vm", "ct"):
                cur = self.b.latest(v) if not self.busy else None
                psi = self.b.psi_pct(v)
                if cur and now - cur[0] < 3.5:
                    row[v] = {"eps": round(cur[1], 1), "p95": round(cur[2], 3),
                              "psi": None if psi is None else round(psi, 1)}
                    if self.n == 0 and now > self._settle_until and not self.busy:
                        self._base_buf[v].append((cur[1], cur[2]))
                        if len(self._base_buf[v]) >= 5:
                            self.base[v] = {"eps": round(statistics.median(x[0] for x in self._base_buf[v]), 1),
                                            "p95": round(statistics.median(x[1] for x in self._base_buf[v]), 3)}
                else:
                    row[v] = None
            if tick % 2 == 0:
                try:
                    self.place = self.b.placement()
                except Exception:                            # noqa: BLE001
                    pass
            tick += 1
            with self.lock:
                self.seq += 1
                row["seq"] = self.seq
                self.samples.append(row)

    # ---- HTTP view
    def state(self, since: int) -> dict:
        with self.lock:
            return {"seq": self.seq, "now": time.time(), "n": self.n, "stress": self.stress, "policy": self.policy,
                    "busy": self.busy, "error": self.error, "base": self.base, "placement": self.place,
                    "simulated": self.b.simulated, "queued": self.actions.qsize(),
                    "samples": [s for s in self.samples if s["seq"] > since], "events": list(self.events)}


def make_handler(demo: Demo):
    page = (HERE / "index.html").read_bytes()

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):                           # keep the terminal for demo events
            pass

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, obj) -> None:
            self._send(code, json.dumps(obj).encode(), "application/json")

        def do_GET(self):
            if self.path in ("/", "/index.html"):
                return self._send(200, page, "text/html; charset=utf-8")
            if self.path.startswith("/api/state"):
                m = re.search(r"since=(\d+)", self.path)
                return self._json(200, demo.state(int(m.group(1)) if m else 0))
            self._json(404, {"error": "not found"})

        def do_POST(self):
            try:
                body = json.loads(self.rfile.read(min(int(self.headers.get("Content-Length") or 0), 2000)) or b"{}")
            except ValueError:
                return self._json(400, {"error": "bad json"})
            if demo.busy:
                return self._json(409, {"error": "busy"})
            if self.path == "/api/neighbours":
                n, stress = body.get("n"), body.get("stress", demo.stress)
                if not isinstance(n, int) or not 0 <= n <= 8 or stress not in STRESSES:
                    return self._json(400, {"error": "n must be 0..8, stress one of cpu|mem|io"})
                demo.actions.put(("neighbours", (n, stress)))
            elif self.path == "/api/policy":
                if body.get("policy") not in POLICIES:
                    return self._json(400, {"error": "policy must be default|float|dedicated"})
                demo.busy = "Switching CPU placement…"
                demo.actions.put(("policy", body["policy"]))
            elif self.path == "/api/rebaseline":
                demo.actions.put(("rebaseline", None))
            else:
                return self._json(404, {"error": "not found"})
            self._json(200, {"ok": True})

    return H


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--bind", default="0.0.0.0", help="address to listen on (default: all interfaces)")
    a = ap.parse_args()

    sim = os.environ.get("DEMO_SIM") == "1"
    if not sim and os.geteuid() != 0:
        sys.exit("run as root on the Proxmox host (or DEMO_SIM=1 to rehearse without Proxmox)")
    backend = SimBackend() if sim else PveBackend()
    demo = Demo(backend)
    threading.Thread(target=demo.worker, daemon=True).start()
    threading.Thread(target=demo.sampler, daemon=True).start()
    srv = ThreadingHTTPServer((a.bind, a.port), make_handler(demo))

    def bye(*_):
        log("stopping neighbour load and benchmarks…")
        demo.halt.set()
        try:
            backend.shutdown()
        finally:
            os._exit(0)

    signal.signal(signal.SIGINT, bye)
    signal.signal(signal.SIGTERM, bye)
    host = "localhost" if sim else backend.pve_ip
    log(("SIMULATED data — " if sim else "") + f"demo page: http://{host}:{a.port}/   (Ctrl+C stops everything)")
    srv.serve_forever()


if __name__ == "__main__":
    main()
