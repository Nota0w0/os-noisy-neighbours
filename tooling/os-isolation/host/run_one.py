#!/usr/bin/env python3
"""One experimental run (RUN-01), following the protocol in README §Run protocol.

    python3 host/run_one.py --arm vm --dim cpu --n 8            # ad-hoc run -> results/adhoc/
    python3 host/run_one.py --arm ct --dim io  --n 0 --intensity high --label smoke

run_matrix.py imports execute_run() from here, so an ad-hoc run and a
matrix run go through exactly the same code.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import time
from pathlib import Path

import cfg as cfgmod
from pvelib import CmdError, check_arms, get_platform, host_psi_avg10, last_json_line, load_platform_info
from sampler import Sampler

RUN_FIELDS = ["run_id", "ts_start", "ts_end", "config_version", "platform", "block", "order_in_block",
              "arm", "dim", "n", "intensity", "rep", "p95_ms", "mean_ms", "max_ms", "throughput", "unit",
              "nbr_active_start", "nbr_active_end", "guest_steal_ticks", "cooldown_wait_s",
              "host_cpu_psi10_start", "host_io_psi10_start", "ok", "note"]
SAMPLE_FIELDS = ["run_id", "t", "target", "metric", "value"]


def now_iso() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def append_csv(path: Path, fields: list[str], rows: list) -> None:
    new = not path.exists()
    with open(path, "a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(fields)
        for r in rows:
            w.writerow([r.get(k, "") for k in fields] if isinstance(r, dict) else r)


def cooldown(c: dict) -> tuple[float, float, float]:
    """Wait until host CPU and I/O PSI (some avg10) are below threshold."""
    thr, max_s = float(c["bench"]["cooldown_psi_avg10"]), float(c["bench"]["cooldown_max_s"])
    t0 = time.monotonic()
    while True:
        cpu, io = host_psi_avg10("cpu"), host_psi_avg10("io")
        if (cpu < thr and io < thr) or time.monotonic() - t0 >= max_s:
            return round(time.monotonic() - t0, 1), cpu, io
        time.sleep(2)


def bench_argv(c: dict, dim: str) -> list[str]:
    b = c["bench"]
    if dim == "cpu":
        return ["/opt/bench/bench_cpu.sh", str(b["duration_s"]), str(b["cpu_threads"])]
    return ["/opt/bench/bench_io.sh", "run", str(b["duration_s"]), str(b["io_iodepth"]),
            str(b["io_file_gb"]), str(b["io_bs"])]


def execute_run(plat, c: dict, platform: str, spec: dict, out_dir: Path) -> dict:
    """Run one (arm, dim, n, intensity) measurement. Never raises; failures -> ok=0 + note."""
    arm, dim, n, inten = spec["arm"], spec["dim"], int(spec["n"]), spec["intensity"]
    b = c["bench"]
    ids = plat.nbrs[:n]
    row = {k: "" for k in RUN_FIELDS}
    row.update(spec, ts_start=now_iso(), config_version=c["config_version"], platform=platform, ok=0)
    samples: list[tuple] = []
    raw: dict = {"spec": spec}
    try:
        if n > len(plat.nbrs):
            raise CmdError(f"n={n} but only {len(plat.nbrs)} neighbours are configured")
        plat.ensure_victims_running([arm])
        plat.ensure_neighbours_running()
        plat.stop_load(plat.nbrs)                       # clean slate
        row["cooldown_wait_s"], row["host_cpu_psi10_start"], row["host_io_psi10_start"] = cooldown(c)
        plat.drop_caches(arm)

        if ids:
            plat.start_load(ids, dim, c["intensity"][inten], int(b["warmup_s"]) + int(b["duration_s"]) + 30)
        time.sleep(float(b["warmup_s"]))
        row["nbr_active_start"] = plat.count_load(ids) if ids else 0
        if row["nbr_active_start"] != n:
            raise CmdError(f"only {row['nbr_active_start']}/{n} neighbours under load after warm-up")

        victim_cg = plat.victim_cgroup(arm)
        if victim_cg is None or not (victim_cg / "cpu.stat").exists():
            raise CmdError(f"victim cgroup not found for arm={arm} ({victim_cg})")
        targets = {"victim": victim_cg}
        for k, i in enumerate(ids, start=1):
            targets[f"nbr{k}"] = plat.nbr_cgroup(i)
        vcpu = None
        if arm == "vm" and plat.vm_pid() and plat.vcpu_tids():
            vcpu = (plat.vm_pid(), plat.vcpu_tids())

        steal0 = plat.guest_steal() if arm == "vm" else None
        sampler = Sampler(spec["run_id"], targets, vcpu, interval=1.0)
        sampler.start()
        try:
            out = plat.victim_exec(arm, bench_argv(c, dim), timeout=float(b["duration_s"]) + 120)
        finally:
            samples = sampler.stop()
        steal1 = plat.guest_steal() if arm == "vm" else None
        row["nbr_active_end"] = plat.count_load(ids) if ids else 0

        res = last_json_line(out)
        raw["victim"] = res
        row.update(p95_ms=res["p95_ms"], mean_ms=res["mean_ms"], max_ms=res["max_ms"],
                   throughput=res["throughput"], unit=res["unit"])
        if steal0 is not None and steal1 is not None:
            row["guest_steal_ticks"] = steal1 - steal0
        if row["nbr_active_end"] != n:
            raise CmdError(f"neighbour load ended early: {row['nbr_active_end']}/{n} still running")
        row["ok"] = 1
    except Exception as e:                               # noqa: BLE001 — record every failure
        row["note"] = f"{type(e).__name__}: {e}".replace("\n", " ")[:400]
    finally:
        try:
            plat.stop_load(ids)
        except Exception:                                # noqa: BLE001
            pass
        row["ts_end"] = now_iso()

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "raw").mkdir(exist_ok=True)
    raw["row"] = row
    (out_dir / "raw" / f"{spec['run_id']}.json").write_text(json.dumps(raw, indent=1, default=str))
    append_csv(out_dir / "runs.csv", RUN_FIELDS, [row])
    append_csv(out_dir / "samples.csv", SAMPLE_FIELDS, samples)
    return row


def main() -> None:
    ap = argparse.ArgumentParser(description="one ad-hoc experimental run")
    ap.add_argument("--arm", choices=["vm", "ct"], required=True)
    ap.add_argument("--dim", choices=["cpu", "io"], required=True)
    ap.add_argument("--n", type=int, required=True)
    ap.add_argument("--intensity", default="high")
    ap.add_argument("--label", default="adhoc")
    a = ap.parse_args()

    c = cfgmod.load()
    info = load_platform_info()
    check_arms(info["verdict"], [a.arm])
    plat = get_platform(c)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    spec = dict(run_id=f"{a.label}-{stamp}-{a.arm}-{a.dim}-n{a.n}-{a.intensity}", block=0, order_in_block=0,
                arm=a.arm, dim=a.dim, n=a.n, intensity=a.intensity, rep=0)
    out = cfgmod.REPO / "results" / a.label
    try:
        row = execute_run(plat, c, info["verdict"], spec, out)
    finally:
        plat.close()
    status = "OK" if row["ok"] == 1 else "FAILED"
    print(f"[{status}] {spec['run_id']}  p95={row['p95_ms']} ms  throughput={row['throughput']} {row['unit']}"
          f"  neighbours={row['nbr_active_start']}/{a.n}  {row['note']}")
    print(f"        -> {out / 'runs.csv'}")
    raise SystemExit(0 if row["ok"] == 1 else 1)


if __name__ == "__main__":
    main()
