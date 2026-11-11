#!/usr/bin/env python3
"""Run the experiment matrix in randomised blocks (RUN-02 / RUN-03 / RUN-04).

    python3 host/run_matrix.py --dry-run                       # show the plan and ETA
    python3 host/run_matrix.py --label baseline --neighbours 0 # RUN-02: 4 conditions x 5 reps
    python3 host/run_matrix.py --label pilot --reps 1          # RUN-03: every n once
    python3 host/run_matrix.py --label main                    # RUN-04: full matrix from config

Each block contains every condition once, in its own random order, so
slow drift (SSD state, temperature) spreads evenly over all conditions.
Re-running the same command resumes: conditions already ok=1 in
results/<label>/runs.csv are skipped. Ctrl-C stops all neighbour load.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import itertools
import json
import random
import shutil
import signal
import sys
import time

import cfg as cfgmod
from pvelib import check_arms, get_platform, load_platform_info
from run_one import execute_run


def build_plan(arms, dims, ns, intens, reps, seed, label):
    plan = []
    conds = list(itertools.product(arms, dims, ns, intens))
    for block in range(1, reps + 1):
        order = conds[:]
        random.Random(seed * 1000 + block).shuffle(order)
        for k, (arm, dim, n, inten) in enumerate(order, start=1):
            plan.append(dict(run_id=f"{label}-b{block}-{k:02d}-{arm}-{dim}-n{n}-{inten}",
                             block=block, order_in_block=k, arm=arm, dim=dim, n=n,
                             intensity=inten, rep=block))
    return plan


def done_keys(runs_csv):
    keys = set()
    if runs_csv.exists():
        with open(runs_csv, newline="") as f:
            for r in csv.DictReader(f):
                if r.get("ok") == "1":
                    keys.add((int(r["block"]), r["arm"], r["dim"], int(r["n"]), r["intensity"]))
    return keys


def main() -> None:
    c = cfgmod.load()
    m = c["matrix"]
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--label", default=dt.date.today().isoformat(), help="results/<label>/ (default: today)")
    ap.add_argument("--arms", nargs="+", default=m["arms"], choices=["vm", "ct"])
    ap.add_argument("--dims", nargs="+", default=m["dims"], choices=["cpu", "io"])
    ap.add_argument("--neighbours", nargs="+", type=int, default=m["neighbours"])
    ap.add_argument("--intensities", nargs="+", default=m["intensities"])
    ap.add_argument("--reps", type=int, default=int(m["reps"]))
    ap.add_argument("--seed", type=int, default=int(m["seed"]))
    ap.add_argument("--retries", type=int, default=1, help="extra attempts per failed run")
    ap.add_argument("--max-runs", type=int, default=0, help="stop after this many runs (0 = all)")
    ap.add_argument("--allow-nested", action="store_true", help="accept a NESTED_* platform verdict")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    for i in a.intensities:
        if i not in c["intensity"]:
            ap.error(f"unknown intensity '{i}' (config has: {', '.join(c['intensity'])})")
    out = cfgmod.REPO / "results" / a.label
    plan = build_plan(a.arms, a.dims, a.neighbours, a.intensities, a.reps, a.seed, a.label)
    done = done_keys(out / "runs.csv")
    todo = [p for p in plan if (p["block"], p["arm"], p["dim"], p["n"], p["intensity"]) not in done]
    if a.max_runs:
        todo = todo[: a.max_runs]
    b = c["bench"]
    per_run = int(b["warmup_s"]) + int(b["duration_s"]) + 35
    print(f"plan: {len(plan)} runs ({len(done)} already done) -> {len(todo)} to run, "
          f"~{len(todo) * per_run / 3600:.1f} h  -> {out}")

    if a.dry_run:
        for p in todo[:25]:
            print(f"  b{p['block']} #{p['order_in_block']:02d}  {p['arm']:2s} {p['dim']:3s} n={p['n']} {p['intensity']}")
        if len(todo) > 25:
            print(f"  ... {len(todo) - 25} more")
        return

    info = load_platform_info()
    verdict = info["verdict"]
    check_arms(verdict, a.arms, allow_nested=a.allow_nested)

    out.mkdir(parents=True, exist_ok=True)
    shutil.copy(c["_path"], out / "experiment.toml")
    (out / "platform.json").write_text(json.dumps(info, indent=2))

    plat = get_platform(c)

    def _abort(signum, _frame):
        print(f"\nsignal {signum}: stopping neighbour load …", flush=True)
        try:
            plat.stop_load(plat.nbrs)
        finally:
            plat.close()
        sys.exit(130)

    signal.signal(signal.SIGINT, _abort)
    signal.signal(signal.SIGTERM, _abort)

    t0 = time.monotonic()
    ok = fail = 0
    try:
        for idx, spec in enumerate(todo, start=1):
            for attempt in range(1 + a.retries):
                row = execute_run(plat, c, verdict, spec, out)
                tag = "OK  " if row["ok"] == 1 else "FAIL"
                print(f"[{idx:3d}/{len(todo)}] {tag} {spec['run_id']}  p95={row['p95_ms'] or '-'} ms  "
                      f"thr={row['throughput'] or '-'}  {row['note'][:120]}", flush=True)
                if row["ok"] == 1:
                    ok += 1
                    break
            else:
                fail += 1
            elapsed = time.monotonic() - t0
            eta = elapsed / idx * (len(todo) - idx)
            if idx % 5 == 0 or idx == len(todo):
                print(f"      elapsed {elapsed / 60:.0f} min, ETA {eta / 60:.0f} min", flush=True)
    finally:
        plat.close()
    print(f"done: {ok} ok, {fail} failed  -> {out / 'runs.csv'}")
    sys.exit(0 if fail == 0 else 1)


if __name__ == "__main__":
    main()
