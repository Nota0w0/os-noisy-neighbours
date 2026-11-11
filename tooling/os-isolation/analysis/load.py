"""Load results/<label>/ directories into pandas (ANA-01..04).

    from load import load_results, run_attribution
    res = load_results(["results/main", "results/baseline"])
    res.runs      # every row of runs.csv (ok=0 included), de-duplicated by run_id
    res.ok        # ok == 1 only, with per-run attribution columns merged in
    res.meta      # config, platform info, verdicts, config versions

Counters in samples.csv are cumulative; a run's rate is (last - first) /
(t_last - t_first) over the samples taken while the victim benchmark ran.
"""
from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

USER_HZ = 100          # /proc/stat tick rate inside an Ubuntu guest

NUMERIC = ["block", "order_in_block", "n", "rep", "p95_ms", "mean_ms", "max_ms", "throughput",
           "nbr_active_start", "nbr_active_end", "guest_steal_ticks", "cooldown_wait_s",
           "host_cpu_psi10_start", "host_io_psi10_start", "ok"]


@dataclass
class Results:
    runs: pd.DataFrame
    ok: pd.DataFrame
    meta: dict = field(default_factory=dict)


def _read_meta(d: Path) -> dict:
    meta: dict = {}
    if (d / "experiment.toml").exists():
        with open(d / "experiment.toml", "rb") as f:
            meta["config"] = tomllib.load(f)
    if (d / "platform.json").exists():
        meta["platform"] = json.loads((d / "platform.json").read_text())
    return meta


def run_attribution(samples: pd.DataFrame) -> pd.DataFrame:
    """One row per run_id with rates derived from the cumulative counters."""
    if samples.empty:
        return pd.DataFrame(columns=["run_id"])
    s = samples.sort_values(["run_id", "target", "metric", "t"])
    g = s.groupby(["run_id", "target", "metric"], sort=False)
    d = g.agg(t0=("t", "first"), t1=("t", "last"), v0=("value", "first"), v1=("value", "last")).reset_index()
    d["dt"] = (d["t1"] - d["t0"]).replace(0, np.nan)
    d["delta"] = d["v1"] - d["v0"]
    d["rate"] = d["delta"] / d["dt"]

    def pick(target, metric, col="rate"):
        m = d[(d["target"] == target) & (d["metric"] == metric)]
        return m.set_index("run_id")[col]

    def nbr_sum(metric):
        m = d[d["target"].str.startswith("nbr") & (d["metric"] == metric)]
        return m.groupby("run_id")["rate"].sum()

    out = pd.DataFrame(index=pd.Index(d["run_id"].unique(), name="run_id"))
    us_to_pct = 1e-4                      # µs of stall per s of wall time -> %
    out["victim_cpu_some_pct"] = pick("victim", "cpu.pressure.some.total") * us_to_pct
    out["victim_cpu_full_pct"] = pick("victim", "cpu.pressure.full.total") * us_to_pct
    out["victim_io_some_pct"] = pick("victim", "io.pressure.some.total") * us_to_pct
    out["victim_io_full_pct"] = pick("victim", "io.pressure.full.total") * us_to_pct
    out["victim_mem_some_pct"] = pick("victim", "memory.pressure.some.total") * us_to_pct
    out["victim_cpu_cores"] = pick("victim", "cpu.usage_usec") / 1e6
    out["victim_throttled_us"] = pick("victim", "cpu.throttled_usec", "delta")
    out["victim_read_mibs"] = pick("victim", "io.rbytes") / 2**20
    # vCPU threads (VM only): wait-for-CPU as % of wall time, averaged over vCPUs
    threads = pick("victim", "vcpu.threads", "v1")
    out["vcpu_wait_pct"] = pick("victim", "vcpu.run_delay_ns") / 1e9 * 100 / threads
    out["vcpu_run_pct"] = pick("victim", "vcpu.run_ns") / 1e9 * 100 / threads
    out["nbr_cpu_cores"] = nbr_sum("cpu.usage_usec") / 1e6
    out["nbr_write_mibs"] = nbr_sum("io.wbytes") / 2**20
    out["host_cpu_some_pct"] = pick("host", "host.cpu.pressure.some.total") * us_to_pct
    out["host_io_some_pct"] = pick("host", "host.io.pressure.some.total") * us_to_pct
    busy = pick("host", "host.stat.busy_jiffies", "delta")
    total = pick("host", "host.stat.total_jiffies", "delta")
    out["host_busy_pct"] = busy / total.replace(0, np.nan) * 100
    out["sample_window_s"] = d[d["target"] == "host"].groupby("run_id")["dt"].max()
    return out.reset_index()


def load_results(dirs: list[str | Path]) -> Results:
    runs_l, samp_l, meta = [], [], {}
    for d in map(Path, dirs):
        if not (d / "runs.csv").exists():
            raise SystemExit(f"{d}/runs.csv not found")
        r = pd.read_csv(d / "runs.csv", dtype={"note": str})
        r["source"] = d.name
        runs_l.append(r)
        if (d / "samples.csv").exists():
            samp_l.append(pd.read_csv(d / "samples.csv"))
        for k, v in _read_meta(d).items():
            meta.setdefault(k, v)
    runs = pd.concat(runs_l, ignore_index=True).drop_duplicates("run_id", keep="last")
    for col in NUMERIC:
        if col in runs:
            runs[col] = pd.to_numeric(runs[col], errors="coerce")
    runs["note"] = runs["note"].fillna("")

    ok = runs[runs["ok"] == 1].copy()
    samples = pd.concat(samp_l, ignore_index=True) if samp_l else pd.DataFrame()
    ok = ok.merge(run_attribution(samples), on="run_id", how="left")

    cfg = meta.get("config", {})
    cores = float(cfg.get("victim", {}).get("cores", 2))
    dur = float(cfg.get("bench", {}).get("duration_s", 60))
    ok["guest_steal_pct"] = ok["guest_steal_ticks"] / (dur * USER_HZ * cores) * 100

    meta["verdicts"] = sorted(runs["platform"].dropna().astype(str).unique())
    meta["config_versions"] = sorted(runs["config_version"].dropna().astype(str).unique())
    meta["dirs"] = [str(d) for d in dirs]
    return Results(runs=runs, ok=ok, meta=meta)
