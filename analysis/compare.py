#!/usr/bin/env python3
"""Compare two result sets produced by osproj.sh (e.g. the nested pilot and a run on another machine).

    python3 analysis/compare.py results/nested-2026-10-19 results/<label>-<date> [-o compare.md]

Reads summary.csv from both directories and prints Markdown: the platforms, the uncontended baselines
(absolute), the headline numbers behind each pilot finding, and every configuration where the two
machines disagree by more than --tol (default 0.10 of baseline). Standard library only.

All relative values are "performance under interference / same-block baseline": for throughput
metrics below 1 is worse, for latency metrics above 1 is worse.
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

THROUGHPUT = [("cpu_eps", "CPU events/s"), ("mem_mibs", "memory MiB/s"), ("io_iops", "disk IOPS")]
LATENCY = [("cpu_lat_p99_ms", "CPU p99 ms"), ("io_p99_us", "disk p99 µs"), ("wake_p99_us", "wake-up p99 µs")]
POLICIES = ("default", "float", "dedicated")


def load(d: Path) -> dict:
    f = d / "summary.csv"
    if not f.is_file():
        sys.exit(f"{f} not found — pass a results directory that contains summary.csv")
    out = {}
    with f.open(newline="") as fh:
        for r in csv.DictReader(fh):
            num = lambda k: float(r[k]) if r.get(k) not in (None, "") else None   # noqa: E731
            out[(r["policy"], r["victim"], r["stress"], int(r["n"]), r["metric"])] = {
                "abs": num("abs_mean"), "std": num("abs_std"), "rel": num("rel_mean"),
                "lo": num("rel_min"), "hi": num("rel_max"), "count": int(r["count"] or 0)}
    return out


def platform(d: Path) -> str:
    """One line describing the host, from platform.txt (new runs) or meta.txt (pilot)."""
    bits = []
    for name in ("platform.txt", "meta.txt"):
        f = d / name
        if not f.is_file():
            continue
        lines = f.read_text(errors="replace").splitlines()
        for i, line in enumerate(lines):
            s = line.strip()
            if s.lower().startswith("model name"):
                bits.append(s.split(":", 1)[1].strip())
            elif s.startswith("pve-manager/"):
                bits.append("PVE " + s.split("/")[1])
            elif s.startswith("== virtualisation") and i + 1 < len(lines):
                v = lines[i + 1].strip()
                bits.append("bare metal" if v == "none" else f"virtualised ({v})")
            elif s.startswith("NCPU="):
                bits.append(s.split()[0])
        if bits:
            break
    seen, uniq = set(), []
    for b in bits:
        if b not in seen:
            seen.add(b)
            uniq.append(b)
    return ", ".join(uniq) or "no platform.txt / meta.txt"


def fmt(x, nd=2) -> str:
    return "–" if x is None else f"{x:.{nd}f}"


def rng(c) -> str:
    if not c or c["rel"] is None:
        return "–"
    return f"{c['rel']:.2f} ({c['lo']:.2f}–{c['hi']:.2f})"


def table(head: list[str], rows: list[list[str]]) -> str:
    out = ["| " + " | ".join(head) + " |", "|" + "|".join("---" for _ in head) + "|"]
    out += ["| " + " | ".join(r) + " |" for r in rows]
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("a", type=Path, help="first results directory (reference)")
    ap.add_argument("b", type=Path, help="second results directory")
    ap.add_argument("--tol", type=float, default=0.10, help="report configurations whose relative value differs by more than this")
    ap.add_argument("-o", "--out", type=Path, help="also write the Markdown to this file")
    a = ap.parse_args()
    A, B = load(a.a), load(a.b)
    la, lb = a.a.name, a.b.name
    doc: list[str] = [f"# {la} vs {lb}", "",
                      table(["", la, lb], [["platform", platform(a.a), platform(a.b)],
                                           ["repetitions", str(max((c["count"] for c in A.values()), default=0)),
                                            str(max((c["count"] for c in B.values()), default=0))],
                                           ["configurations", str(len({k[:4] for k in A})), str(len({k[:4] for k in B}))]]), ""]

    # ---- baselines: absolute numbers, the only place where the two machines are expected to differ a lot
    rows = []
    for m, name in THROUGHPUT + LATENCY:
        for v in ("vm", "ct"):
            ca, cb = A.get(("default", v, "none", 0, m)), B.get(("default", v, "none", 0, m))
            xa, xb = (ca or {}).get("abs"), (cb or {}).get("abs")
            rows.append([name, v, fmt(xa, 1), fmt(xb, 1), fmt(xb / xa if xa and xb else None)])
    doc += ["## Uncontended baseline (absolute, policy = default)", "", table(["metric", "victim", la, lb, f"{lb} / {la}"], rows), ""]

    # ---- headline numbers behind the pilot findings
    ns = sorted({k[3] for k in list(A) + list(B) if k[2] not in ("none", "post")})
    nmax = ns[-1] if ns else 8
    rows = []
    for pol in POLICIES:
        for v in ("vm", "ct"):
            k = (pol, v, "cpu", nmax, "cpu_eps")
            rows.append([pol, v, rng(A.get(k)), rng(B.get(k))])
    doc += [f"## Finding 1–2: CPU throughput under CPU stress, n = {nmax}",
            "", "Pilot: the container falls behind under `default` and `float`, not under `dedicated`; the VM stays near 0.85.",
            "", table(["policy", "victim", la, lb], rows), ""]

    rows = []
    for pol in POLICIES:
        for v in ("vm", "ct"):
            k = (pol, v, "cpu", nmax, "io_iops")
            rows.append([pol, v, rng(A.get(k)), rng(B.get(k))])
    doc += [f"## Finding 3: disk IOPS under CPU stress, n = {nmax}",
            "", "Pilot: the VM keeps its CPU time but loses most of its I/O unless its CPUs are dedicated.",
            "", table(["policy", "victim", la, lb], rows), ""]

    rows = []
    for n in ns:
        for v in ("vm", "ct"):
            k = ("dedicated", v, "mem", n, "mem_mibs")
            rows.append([str(n), v, rng(A.get(k)), rng(B.get(k))])
    doc += ["## Finding 4a: memory bandwidth under memory stress, policy = dedicated",
            "", "Pilot: both guest types lose the same amount.", "", table(["n", "victim", la, lb], rows), ""]

    rows = []
    for v in ("vm", "ct"):
        for m, name in (("io_iops", "IOPS"), ("io_p99_us", "p99 µs")):
            ca, cb = A.get(("default", v, "io", nmax, m)), B.get(("default", v, "io", nmax, m))
            rows.append([v, name, fmt((ca or {}).get("abs"), 0), fmt((cb or {}).get("abs"), 0)])
    doc += [f"## Finding 4b: absolute disk performance under disk stress, n = {nmax}, policy = default",
            "", "Pilot: VM and container converge to the same absolute numbers.", "", table(["victim", "metric", la, lb], rows), ""]

    # ---- baseline drift (the 'post' run at the end of each block)
    rows = []
    for pol in POLICIES:
        for v in ("vm", "ct"):
            rows.append([pol, v] + [rng(X.get((pol, v, "post", 0, m))) for X in (A, B) for m in ("cpu_eps", "io_iops")])
    doc += ["## Baseline drift within a block (end / start, 1.00 = stable)", "",
            table(["policy", "victim", f"{la} CPU", f"{la} IOPS", f"{lb} CPU", f"{lb} IOPS"], rows), ""]

    # ---- everything that disagrees
    rows = []
    for k in sorted(set(A) & set(B)):
        pol, v, s, n, m = k
        if s in ("none", "post") or m not in dict(THROUGHPUT):
            continue
        ra, rb = A[k]["rel"], B[k]["rel"]
        if ra is None or rb is None or abs(ra - rb) <= a.tol:
            continue
        rows.append([abs(ra - rb), [pol, v, s, str(n), dict(THROUGHPUT)[m], rng(A[k]), rng(B[k]), f"{rb - ra:+.2f}"]])
    rows.sort(key=lambda r: -r[0])
    doc += [f"## Configurations that differ by more than {a.tol:.2f} (throughput relative to baseline)", ""]
    doc += [table(["policy", "victim", "stress", "n", "metric", la, lb, "difference"], [r[1] for r in rows])
            if rows else "None: every throughput configuration agrees within the tolerance."]
    only = len(set(A) ^ set(B))
    if only:
        doc += ["", f"{only} metric rows exist in only one of the two result sets (different matrix or an incomplete run)."]

    text = "\n".join(doc) + "\n"
    sys.stdout.reconfigure(encoding="utf-8")
    print(text)
    if a.out:
        a.out.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
