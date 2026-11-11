#!/usr/bin/env python3
"""Check that the testbed is ready to measure (INF-05 / INF-06).

    python3 host/validate.py            # all checks, ~1 min, writes docs/validation.md
    python3 host/validate.py --seconds 3

Run it after provision.sh and again whenever the host is rebooted or a
guest is rebuilt. Exit code 0 = no FAIL (WARNs are allowed), 1 = at least
one FAIL. Every check prints PASS / WARN / FAIL / SKIP with the evidence,
and the same table goes into docs/validation.md for the report appendix.
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
import time
import traceback
from pathlib import Path

import cfg as cfgmod
from pvelib import check_arms, get_platform, last_json_line, load_platform_info, read_psi
from sampler import Sampler, read_cgroup, read_vcpus

RESULTS: list[tuple[str, str, str]] = []


def record(name: str, status: str, detail: str) -> None:
    RESULTS.append((name, status, detail))
    print(f"  [{status:4s}] {name:38s} {detail}", flush=True)


def run_check(name: str, fn) -> None:
    """fn() returns (status, detail); an exception is a FAIL with its message."""
    try:
        status, detail = fn()
    except Exception as e:                       # noqa: BLE001 — report, keep going
        status, detail = "FAIL", f"{type(e).__name__}: {e}".replace("\n", " ")[:300]
        if "--debug" in sys.argv:
            traceback.print_exc()
    record(name, status, detail)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seconds", type=int, default=5, help="length of each short benchmark")
    ap.add_argument("--debug", action="store_true", help="print tracebacks")
    ap.add_argument("--arms", nargs="+", default=["vm", "ct"], choices=["vm", "ct"],
                    help="victims to check (BLOCKED_NO_KVM hosts: --arms ct)")
    a = ap.parse_args()

    c = cfgmod.load()
    info = load_platform_info()
    verdict = info["verdict"]
    print(f"platform verdict: {verdict}   config: {c['_path']} ({c['config_version']})")
    check_arms(verdict, a.arms)

    plat = get_platform(c)
    sim = getattr(plat, "simulated", False)
    secs = a.seconds
    threads = int(c["bench"]["cpu_threads"])
    try:
        _checks(plat, c, sim, secs, threads, a.arms)
    finally:
        try:
            plat.stop_load(plat.nbrs)
        finally:
            plat.close()

    n_fail = sum(1 for r in RESULTS if r[1] == "FAIL")
    n_warn = sum(1 for r in RESULTS if r[1] == "WARN")
    out = (cfgmod.REPO / "cache" / "sim" / "validation.md") if sim else (cfgmod.REPO / "docs" / "validation.md")
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Testbed validation",
        "",
        f"- date: {dt.datetime.now().astimezone().isoformat(timespec='seconds')}",
        f"- platform verdict: `{verdict}`" + ("  (SIMULATED — pipeline test only, never report)" if sim else ""),
        f"- config: `{Path(c['_path']).name}` version `{c['config_version']}`; arms checked: {', '.join(a.arms)}",
        f"- result: **{'FAIL' if n_fail else 'PASS'}** ({n_fail} fail, {n_warn} warn)",
        "",
        "| check | status | evidence |",
        "|---|---|---|",
    ]
    lines += [f"| {n} | {s} | {d.replace('|', '/')} |" for n, s, d in RESULTS]
    out.write_text("\n".join(lines) + "\n")
    print(f"\n{'FAIL' if n_fail else 'PASS'}: {n_fail} fail, {n_warn} warn  -> {out}")
    sys.exit(1 if n_fail else 0)


def _checks(plat, c, sim: bool, secs: int, threads: int, arms) -> None:
    need_n = max(int(x) for x in c["matrix"]["neighbours"])

    # ------------------------------------------------------------ config / host
    print("host")

    def cfg_check():
        if len(plat.nbrs) < need_n:
            return "FAIL", f"matrix needs {need_n} neighbours but ids.nbr_count={len(plat.nbrs)}"
        return "PASS", (f"{len(plat.nbrs)} neighbours x {c['neighbour']['cores']} cores, "
                        f"victim {c['victim']['cores']} cores, max n={need_n}")
    run_check("config consistent", cfg_check)

    def host_psi():
        got = [r for r in ("cpu", "io", "memory") if read_psi(Path(f"/proc/pressure/{r}"))]
        return ("PASS" if len(got) == 3 else "FAIL"), f"/proc/pressure: {', '.join(got) or 'missing'}"
    run_check("host PSI available", host_psi)

    # ------------------------------------------------------------ victims
    print("victims")
    def victims_up():
        plat.ensure_victims_running(arms)
        how = {"vm": "vm answers over ssh", "ct": "ct answers to pct exec"}
        return "PASS", ", ".join(how[x] for x in arms)
    run_check("victims running / reachable", victims_up)

    def nbrs_up():
        plat.ensure_neighbours_running()
        plat.stop_load(plat.nbrs)
        return "PASS", f"{len(plat.nbrs)} neighbour CTs running, load cleared"
    run_check("neighbours running", nbrs_up)

    for arm in arms:
        def cg_files(arm=arm):
            cg = plat.victim_cgroup(arm)
            if cg is None:
                return "FAIL", "cgroup not found"
            missing = [f for f in ("cpu.stat", "cpu.pressure", "io.pressure", "io.stat", "memory.current")
                       if not (cg / f).exists()]
            return ("FAIL" if missing else "PASS"), (f"{cg}" + (f"  missing: {', '.join(missing)}" if missing else ""))
        run_check(f"{arm}: victim cgroup + PSI files", cg_files)

        def cpu_bench(arm=arm):
            cg = plat.victim_cgroup(arm)
            before = read_cgroup(cg).get("cpu.usage_usec", 0)
            out = plat.victim_exec(arm, ["/opt/bench/bench_cpu.sh", str(secs), str(threads)], timeout=secs + 60)
            used = read_cgroup(cg).get("cpu.usage_usec", 0) - before
            res = last_json_line(out)
            floor = 0.4 * secs * threads * 1e6
            status = "PASS" if used >= floor and res["throughput"] > 0 else "FAIL"
            return status, (f"{res['throughput']:.0f} ev/s, p95 {res['p95_ms']} ms; cgroup cpu +{used / 1e6:.1f} s "
                            f"(need >= {floor / 1e6:.1f} s, i.e. the cgroup really contains the victim)")
        run_check(f"{arm}: cpu benchmark + cgroup accounting", cpu_bench)

        def io_bench(arm=arm):
            b = c["bench"]
            out = plat.victim_exec(arm, ["/opt/bench/bench_io.sh", "run", str(secs), str(b["io_iodepth"]),
                                         str(b["io_file_gb"]), str(b["io_bs"])], timeout=secs + 120)
            res = last_json_line(out)
            return ("PASS" if res["throughput"] > 0 else "FAIL"), f"{res['throughput']:.0f} IOPS, p95 {res['p95_ms']} ms"
        run_check(f"{arm}: io benchmark", io_bench)

    # ------------------------------------------------------------ VM-only probes
    print("vm probes")
    if "vm" not in arms:
        record("vm: vCPU threads + schedstat", "SKIP", "VM arm not checked")
        record("vm: guest steal counter", "SKIP", "VM arm not checked")
    elif sim:
        record("vm: vCPU threads + schedstat", "SKIP", "simulated platform has no QEMU process")
        record("vm: guest steal counter", "SKIP", "simulated platform")
    else:
        def vcpu():
            pid, tids = plat.vm_pid(), plat.vcpu_tids()
            if not pid or not tids:
                return "FAIL", f"qemu pid={pid}, vCPU tids={tids} (look for 'CPU n/KVM' threads)"
            comms = {Path(f"/proc/{pid}/task/{t}/comm").read_text().strip() for t in tids}
            if any(cm.endswith("/TCG") for cm in comms):
                return "FAIL", ("VM runs under TCG software emulation (kvm: 0), not KVM — VM results would be "
                                "meaningless. Fix /dev/kvm (README §Platform) and set `qm set <vmid> --kvm 1`.")
            r0 = read_vcpus(pid, tids)
            plat.victim_exec("vm", ["/opt/bench/bench_cpu.sh", "3", str(threads)], timeout=60)
            r1 = read_vcpus(pid, tids)
            ran = (r1.get("vcpu.run_ns", 0) - r0.get("vcpu.run_ns", 0)) / 1e9
            delay = (r1.get("vcpu.run_delay_ns", 0) - r0.get("vcpu.run_delay_ns", 0)) / 1e9
            want = int(c["victim"]["cores"])
            status = "PASS" if ran > 1.0 and len(tids) == want else ("WARN" if ran > 1.0 else "FAIL")
            return status, f"pid {pid}, {len(tids)} vCPU threads (expect {want}); 3 s busy -> run {ran:.1f} s, wait {delay:.2f} s"
        run_check("vm: vCPU threads + schedstat", vcpu)

        def steal():
            s = plat.guest_steal()
            return ("PASS", f"/proc/stat steal = {s} ticks") if s is not None else ("WARN", "could not read guest /proc/stat")
        run_check("vm: guest steal counter", steal)

    # ------------------------------------------------------------ neighbours
    print("neighbours")

    def nbr_cgroups():
        missing = [i for i in plat.nbrs if plat.nbr_cgroup(i) is None]
        return ("FAIL", f"no cgroup for CT {missing}") if missing else ("PASS", f"all {len(plat.nbrs)} resolved")
    run_check("neighbour cgroups", nbr_cgroups)

    first = plat.nbrs[:1]
    for dim in ("cpu", "io"):
        def load(dim=dim):
            cg = plat.nbr_cgroup(first[0])
            u0 = read_cgroup(cg) if cg else {}
            plat.start_load(first, dim, c["intensity"]["high"], 30)
            time.sleep(4)
            on = plat.count_load(first)
            u1 = read_cgroup(cg) if cg else {}
            plat.stop_load(first)
            time.sleep(1.5)
            off = plat.count_load(first)
            if dim == "cpu":
                moved = (u1.get("cpu.usage_usec", 0) - u0.get("cpu.usage_usec", 0)) / 1e6
                evidence = f"cgroup cpu +{moved:.1f} s in 4 s"
            else:
                moved = (u1.get("io.wbytes", 0) - u0.get("io.wbytes", 0)) / 2**20 if not sim else 1
                evidence = f"cgroup wrote {moved:.0f} MiB in 4 s" if not sim else "io.stat not modelled in sim"
            status = "PASS" if on == 1 and off == 0 and moved > 0 else "FAIL"
            return status, f"CT {first[0]}: running={on} after start, {off} after stop; {evidence}"
        run_check(f"neighbour {dim} load start/stop", load)

    # ------------------------------------------------------------ sampler
    print("sampler")

    def sampler():
        targets = {"victim": plat.victim_cgroup(arms[-1]), "nbr1": plat.nbr_cgroup(first[0])}
        s = Sampler("validate", targets, None, interval=0.5)
        s.start()
        time.sleep(2.2)
        rows = s.stop()
        metrics = {(r[2], r[3]) for r in rows}
        need = [("victim", "cpu.usage_usec"), ("victim", "cpu.pressure.some.total"),
                ("victim", "io.pressure.some.total"), ("nbr1", "cpu.usage_usec"),
                ("host", "host.cpu.pressure.some.total"), ("host", "host.stat.busy_jiffies")]
        missing = [f"{t}:{m}" for t, m in need if (t, m) not in metrics]
        ticks = len({r[1] for r in rows})
        return (("FAIL", f"missing {missing}") if missing
                else ("PASS", f"{len(rows)} rows, {ticks} ticks, {len(metrics)} distinct series"))
    run_check("sampler collects all series", sampler)


if __name__ == "__main__":
    main()
