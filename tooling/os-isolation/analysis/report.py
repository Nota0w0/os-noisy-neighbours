#!/usr/bin/env python3
"""Turn results/<label>/ into tables, figures and summary.md (ANA-01 .. ANA-05).

    python3 analysis/report.py results/main
    python3 analysis/report.py results/main results/baseline     # pool several result dirs
    python3 analysis/report.py results/main --out report/figs

Output (default <first dir>/analysis/):
  summary.md                 every table below + automatic notes; paste-ready for the report
  per_run.csv                each ok run with its normalised metrics and counter rates
  baseline.csv               RQ1 + GATE-1: medians and CV at n = 0
  slowdown.csv               S (p95) and R (throughput) per condition, IQR + bootstrap 95% CI
  density.csv                n*: smallest n with S >= 2, interpolated, with an interval
  attribution.csv            median counter rates per condition
  spearman.csv               rank correlation between slowdown and each counter
  vm_vs_ct.csv               ANA-05: is the container's isolation worse than the VM's? (permutation test)
  fig_slowdown_<int>.png     fig_attribution_<int>.png     fig_explain.png     fig_baseline.png

Analysis runs anywhere with Python 3.11 + pandas, numpy, scipy, matplotlib
(see analysis/requirements.txt) — it does not need the Proxmox host.
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt                       # noqa: E402
import numpy as np                                    # noqa: E402
import pandas as pd                                   # noqa: E402
from matplotlib.ticker import FixedLocator, FuncFormatter, MaxNLocator, NullLocator  # noqa: E402
from scipy import stats                               # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from load import load_results                         # noqa: E402

THRESH = 2.0                                          # n* threshold on S
GATE = {"throughput": 5.0, "p95_ms": 10.0}            # GATE-1: max CV (%)
B = 2000                                              # bootstrap resamples
RNG = np.random.default_rng(20261012)

ARM = {"vm": dict(color="#0072B2", marker="o", label="KVM VM", dx=-0.09),
       "ct": dict(color="#D55E00", marker="s", label="LXC CT", dx=+0.09)}
DIM_TITLE = {"cpu": "CPU contention  (victim: sysbench cpu)",
             "io": "Disk I/O contention  (victim: fio 4k randread)"}
METRICS = {
    "cpu": [("victim_cpu_some_pct", "cgroup cpu.pressure some %"),
            ("victim_cpu_full_pct", "cgroup cpu.pressure full %"),
            ("vcpu_wait_pct", "vCPU run-delay % (VM)"),
            ("guest_steal_pct", "guest steal % (VM)"),
            ("host_cpu_some_pct", "host cpu PSI some %")],
    "io": [("victim_io_some_pct", "cgroup io.pressure some %"),
           ("victim_io_full_pct", "cgroup io.pressure full %"),
           ("host_io_some_pct", "host io PSI some %"),
           ("nbr_write_mibs", "neighbour writes MiB/s")],
}


# ---------------------------------------------------------------- helpers
def cv(x) -> float:
    x = np.asarray(x, float)
    x = x[~np.isnan(x)]
    return np.nan if len(x) < 2 or x.mean() == 0 else x.std(ddof=1) / x.mean() * 100


def q(x, p) -> float:
    x = np.asarray(x, float)
    x = x[~np.isnan(x)]
    return float(np.percentile(x, p)) if len(x) else np.nan


def boot_ratio(x, base) -> tuple[float, float]:
    """Bootstrap 95% CI of median(x) / median(base), resampling both groups."""
    x = np.asarray(x, float)
    base = np.asarray(base, float)
    x, base = x[~np.isnan(x)], base[~np.isnan(base)]
    if len(x) < 2 or len(base) < 2:
        return np.nan, np.nan
    r = (np.median(RNG.choice(x, (B, len(x))), axis=1) /
         np.median(RNG.choice(base, (B, len(base))), axis=1))
    lo, hi = np.percentile(r, [2.5, 97.5])
    return float(lo), float(hi)


def crossing(ns, s, thr=THRESH):
    """First n where s >= thr, linearly interpolated between measured points."""
    pts = [(n, v) for n, v in zip(ns, s) if not np.isnan(v)]
    for (n0, v0), (n1, v1) in zip(pts, pts[1:]):
        if v0 >= thr:
            return float(n0), (n0, n0)
        if v1 >= thr:
            return n0 + (thr - v0) * (n1 - n0) / (v1 - v0), (n0, n1)
    return np.nan, None


def fmt(v) -> str:
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "–"
    if isinstance(v, (float, np.floating)):
        a = abs(v)
        return f"{v:,.0f}" if a >= 1000 else f"{v:.1f}" if a >= 10 else f"{v:.2f}"
    return str(v)


def md_table(df: pd.DataFrame) -> str:
    cols = list(df.columns)
    out = ["| " + " | ".join(cols) + " |", "|" + "|".join("---" for _ in cols) + "|"]
    for _, r in df.iterrows():
        out.append("| " + " | ".join(fmt(r[c]) for c in cols) + " |")
    return "\n".join(out)


def pval(p) -> str:
    return "–" if p is None or np.isnan(p) else ("<0.001" if p < 0.001 else f"{p:.3f}")


# ---------------------------------------------------------------- analysis
def baseline_table(ok: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for (arm, dim), g in ok[ok["n"] == 0].groupby(["arm", "dim"]):
        c_thr, c_p95 = cv(g["throughput"]), cv(g["p95_ms"])
        gate = len(g) >= 3 and c_thr < GATE["throughput"] and c_p95 < GATE["p95_ms"]
        rows.append(dict(arm=arm, dim=dim, runs=len(g), unit=g["unit"].iloc[0],
                         throughput_median=g["throughput"].median(), throughput_cv_pct=c_thr,
                         p95_ms_median=g["p95_ms"].median(), p95_cv_pct=c_p95,
                         mean_ms_median=g["mean_ms"].median(), gate1="PASS" if gate else "FAIL"))
    return pd.DataFrame(rows)


def normalise(ok: pd.DataFrame, base: pd.DataFrame) -> pd.DataFrame:
    b = base.set_index(["arm", "dim"])
    key = list(zip(ok["arm"], ok["dim"]))
    ok["p95_norm"] = [p / b.loc[k, "p95_ms_median"] if k in b.index else np.nan for k, p in zip(key, ok["p95_ms"])]
    ok["thr_norm"] = [t / b.loc[k, "throughput_median"] if k in b.index else np.nan
                      for k, t in zip(key, ok["throughput"])]
    return ok


def curves(ok: pd.DataFrame):
    """Yield (arm, dim, intensity, {n: rows}) with the pooled n = 0 baseline in every curve."""
    for (arm, dim), g in ok.groupby(["arm", "dim"]):
        base = g[g["n"] == 0]
        loaded = g[g["n"] > 0]
        for inten in sorted(loaded["intensity"].unique()) or ["high"]:
            groups = {0: base} if len(base) else {}
            for n, gn in loaded[loaded["intensity"] == inten].groupby("n"):
                groups[int(n)] = gn
            yield arm, dim, inten, dict(sorted(groups.items()))


def rho(n, meta) -> float:
    cpus = meta.get("platform", {}).get("cpus")
    nbr = meta.get("platform", {}).get("nbr_cores")
    vc = meta.get("config", {}).get("victim", {}).get("cores", 2)
    return (vc + n * nbr) / cpus if cpus and nbr else np.nan


def slowdown_table(ok: pd.DataFrame, meta: dict) -> pd.DataFrame:
    rows = []
    for arm, dim, inten, groups in curves(ok):
        base = groups.get(0)
        for n, g in groups.items():
            r = dict(arm=arm, dim=dim, intensity=inten, n=n, rho=rho(n, meta) if dim == "cpu" else np.nan,
                     runs=len(g),
                     p95_ms=g["p95_ms"].median(), throughput=g["throughput"].median())
            if base is not None and len(base):
                r["S"] = g["p95_ms"].median() / base["p95_ms"].median()
                r["S_lo"], r["S_hi"] = (1.0, 1.0) if n == 0 else boot_ratio(g["p95_ms"], base["p95_ms"])
                r["S_q1"], r["S_q3"] = q(g["p95_norm"], 25), q(g["p95_norm"], 75)
                r["R"] = g["throughput"].median() / base["throughput"].median()
                r["R_lo"], r["R_hi"] = (1.0, 1.0) if n == 0 else boot_ratio(g["throughput"], base["throughput"])
                r["R_q1"], r["R_q3"] = q(g["thr_norm"], 25), q(g["thr_norm"], 75)
            rows.append(r)
    return pd.DataFrame(rows)


def density_table(sd: pd.DataFrame, meta: dict) -> pd.DataFrame:
    rows = []
    if sd.empty or "S" not in sd:
        return pd.DataFrame()
    for (arm, dim, inten), g in sd.groupby(["arm", "dim", "intensity"]):
        g = g.sort_values("n")
        ns = g["n"].tolist()
        n_star, bracket = crossing(ns, g["S"].tolist())
        early, _ = crossing(ns, g["S_hi"].tolist())        # optimistic CI bound crosses first
        late, _ = crossing(ns, g["S_lo"].tolist())
        if np.isnan(n_star):
            text = f"not reached: S = {g['S'].iloc[-1]:.2f} at n = {ns[-1]}"
        else:
            text = (f"n* ≈ {n_star:.1f}, between n = {bracket[0]} and n = {bracket[1]}; "
                    f"95% CI {early:.1f} – " + (f"{late:.1f}" if not np.isnan(late) else f"> {ns[-1]}"))
        rows.append(dict(arm=arm, dim=dim, intensity=inten, n_star=n_star, ci_early=early, ci_late=late,
                         rho_at_n_star=rho(n_star, meta) if dim == "cpu" and not np.isnan(n_star) else np.nan,
                         S_at_max_n=g["S"].iloc[-1], max_n=ns[-1], summary=text))
    return pd.DataFrame(rows)


def attribution_table(ok: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for arm, dim, inten, groups in curves(ok):
        for n, g in groups.items():
            r = dict(arm=arm, dim=dim, intensity=inten, n=n, runs=len(g), S=g["p95_norm"].median())
            for col, _ in METRICS[dim]:
                if col in g:
                    r[col] = g[col].median()
            r["victim_cpu_cores"] = g["victim_cpu_cores"].median() if "victim_cpu_cores" in g else np.nan
            rows.append(r)
    return pd.DataFrame(rows)


def spearman_table(ok: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for (arm, dim), g in ok.groupby(["arm", "dim"]):
        for col, label in METRICS[dim]:
            if col not in g:
                continue
            d = g[["p95_norm", col]].dropna()
            if len(d) < 5 or d[col].nunique() < 2 or d["p95_norm"].nunique() < 2:
                continue
            r, p = stats.spearmanr(d[col], d["p95_norm"])
            rows.append(dict(arm=arm, dim=dim, counter=label, column=col, runs=len(d), spearman_rho=r, p=p))
    return pd.DataFrame(rows)


def vm_vs_ct_table(ok: pd.DataFrame) -> pd.DataFrame:
    rows = []
    loaded = ok[ok["n"] > 0]
    for (dim, inten, n), g in loaded.groupby(["dim", "intensity", "n"]):
        vm, ct = g[g["arm"] == "vm"]["p95_norm"].dropna(), g[g["arm"] == "ct"]["p95_norm"].dropna()
        if len(vm) < 2 or len(ct) < 2:
            continue
        # exact permutation test on the difference of medians (handles ties from quantised p95)
        p = stats.permutation_test((ct.to_numpy(), vm.to_numpy()),
                                   lambda x, y, axis=-1: np.median(x, axis=axis) - np.median(y, axis=axis),
                                   permutation_type="independent", vectorized=True,
                                   n_resamples=20000, alternative="two-sided", random_state=1).pvalue
        s_vm, s_ct = vm.median(), ct.median()
        ratio = s_ct / s_vm
        # call a difference only if it is both significant and at least 10 % in size
        worse = "–" if p >= 0.05 or abs(ratio - 1) < 0.10 else ("CT" if ratio > 1 else "VM")
        rows.append(dict(dim=dim, intensity=inten, n=int(n), S_vm=s_vm, S_ct=s_ct, ct_over_vm=ratio,
                         runs_vm=len(vm), runs_ct=len(ct), perm_p=p, worse_isolation=worse))
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- figures
def _watermark(fig, meta):
    verdicts = meta.get("verdicts", [])
    if "SIMULATED" in verdicts:
        fig.text(0.5, 0.5, "SIMULATED — pipeline test, not results", ha="center", va="center",
                 fontsize=26, color="0.6", alpha=0.35, rotation=20, zorder=0)
    nested = [v for v in verdicts if v.startswith("NESTED")]
    if nested:
        fig.text(0.01, 0.005, f"platform {', '.join(nested)}: Proxmox itself runs in a VM "
                 "(nested virtualisation confound on the VM arm)", fontsize=7.5, color="0.35")


def _n_axis(ax, ns, dim, meta, xlabel=True):
    ax.set_xticks(range(len(ns)))
    if dim == "cpu" and not np.isnan(rho(1, meta)):
        ax.set_xticklabels([f"{n}\nρ={rho(n, meta):.2f}" for n in ns], fontsize=8.5)
    else:
        ax.set_xticklabels([str(n) for n in ns], fontsize=8.5)
    ax.set_xlim(-0.4, len(ns) - 0.6)
    if xlabel:
        nb = meta.get("platform", {}).get("nbr_cores")
        ax.set_xlabel(f"noisy neighbours n  (LXC, {nb} core{'s' if nb != 1 else ''} each)" if nb
                      else "noisy neighbours n")


def _log2_axis(ax, lo, hi):
    ax.set_yscale("log", base=2)
    lo, hi = min(lo, 0.9), max(hi, 2.2)
    ax.set_ylim(lo / 1.15, hi * 1.15)
    ticks = [t for t in (0.25, 0.5, 1, 2, 4, 8, 16, 32, 64, 128) if lo / 1.15 <= t <= hi * 1.15]
    ax.yaxis.set_major_locator(FixedLocator(ticks))
    ax.yaxis.set_minor_locator(NullLocator())
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"×{v:g}"))


def fig_slowdown(ok, sd, meta, inten, path):
    dims = [d for d in ("cpu", "io") if d in set(sd["dim"])]
    fig, axes = plt.subplots(2, len(dims), figsize=(5.4 * len(dims), 7.6), squeeze=False)
    for j, dim in enumerate(dims):
        sub = sd[(sd["dim"] == dim) & (sd["intensity"] == inten)]
        ns = sorted(sub["n"].unique())
        pos = {n: i for i, n in enumerate(ns)}
        for row, (col, lo, hi, label) in enumerate([("S", "S_lo", "S_hi", "p95 latency slowdown  S(n)"),
                                                    ("R", "R_lo", "R_hi", "throughput ratio  R(n)")]):
            ax = axes[row][j]
            ymin, ymax = 1.0, 1.0
            for arm, st in ARM.items():
                c = sub[sub["arm"] == arm].sort_values("n")
                if c.empty or col not in c:
                    continue
                x = np.array([pos[n] for n in c["n"]]) + st["dx"]
                ax.fill_between(x, c[lo], c[hi], color=st["color"], alpha=0.15, lw=0)
                ax.plot(x, c[col], color=st["color"], marker=st["marker"], lw=2, ms=6, label=st["label"], zorder=3)
                pts = ok[(ok["arm"] == arm) & (ok["dim"] == dim) & ((ok["n"] == 0) | (ok["intensity"] == inten))]
                pts = pts[pts["n"].isin(ns)]
                v = pts["p95_norm" if col == "S" else "thr_norm"]
                jit = RNG.uniform(-0.035, 0.035, len(pts))
                ax.scatter(np.array([pos[n] for n in pts["n"]]) + st["dx"] + jit, v, s=13,
                           color=st["color"], marker=st["marker"], alpha=0.35, lw=0, zorder=2)
                ymin = min(ymin, np.nanmin([c[lo].min(), v.min()]))
                ymax = max(ymax, np.nanmax([c[hi].max(), v.max()]))
            if col == "S":
                _log2_axis(ax, ymin, ymax)
                ax.axhline(THRESH, color="0.3", ls="--", lw=1)
                ax.text(len(ns) - 0.62, THRESH / 1.04, "S = 2 (n* threshold)", fontsize=8, color="0.3",
                        va="top", ha="right")
                ax.set_title(DIM_TITLE[dim], fontsize=11, loc="left")
            else:
                ax.set_ylim(0, max(1.15, ymax * 1.05))
            ax.axhline(1, color="0.6", lw=0.8)
            ax.grid(axis="y", color="0.92")
            ax.set_ylabel(label)
            _n_axis(ax, ns, dim, meta, xlabel=(row == 1))
            for s in ("top", "right"):
                ax.spines[s].set_visible(False)
    axes[0][0].legend(loc="upper left", frameon=False)
    fig.suptitle(f"Victim degradation vs. number of noisy neighbours  (intensity: {inten}; "
                 "line = median, band = bootstrap 95% CI, dots = runs)", fontsize=10.5, x=0.01, ha="left")
    _watermark(fig, meta)
    fig.tight_layout(rect=(0, 0.015, 1, 0.97))
    fig.savefig(path, dpi=200)
    plt.close(fig)


def fig_attribution(at, meta, inten, path):
    dims = [d for d in ("cpu", "io") if d in set(at["dim"])]
    fig, axes = plt.subplots(1, len(dims), figsize=(5.6 * len(dims), 4.4), squeeze=False)
    styles = {"cpu": [("victim_cpu_some_pct", "-", "cpu.pressure some"),
                      ("vcpu_wait_pct", "--", "vCPU run-delay"),
                      ("guest_steal_pct", ":", "guest steal")],
              "io": [("victim_io_some_pct", "-", "io.pressure some"),
                     ("victim_io_full_pct", "--", "io.pressure full")]}
    for j, dim in enumerate(dims):
        ax = axes[0][j]
        sub = at[(at["dim"] == dim) & (at["intensity"] == inten)]
        ns = sorted(sub["n"].unique())
        pos = {n: i for i, n in enumerate(ns)}
        for arm, st in ARM.items():
            c = sub[sub["arm"] == arm].sort_values("n")
            for col, ls, name in styles[dim]:
                if col not in c or c[col].isna().all():
                    continue
                ax.plot([pos[n] for n in c["n"]], c[col], ls=ls, color=st["color"], marker=st["marker"],
                        ms=5, lw=1.8, label=f"{st['label']}: {name}")
        ax.set_ylabel("% of wall time (median of runs)")
        ax.set_ylim(bottom=0)
        ax.grid(axis="y", color="0.92")
        ax.set_title(DIM_TITLE[dim], fontsize=11, loc="left")
        _n_axis(ax, ns, dim, meta)
        ax.legend(fontsize=8, frameon=False, loc="upper left")
        for s in ("top", "right"):
            ax.spines[s].set_visible(False)
    fig.suptitle(f"Where the victim's time goes: host-side stall counters (intensity: {inten})",
                 fontsize=10.5, x=0.01, ha="left")
    _watermark(fig, meta)
    fig.tight_layout(rect=(0, 0.02, 1, 0.95))
    fig.savefig(path, dpi=200)
    plt.close(fig)


def fig_explain(ok, sp, meta, path):
    pairs = [("cpu", "victim_cpu_some_pct", "victim cgroup cpu.pressure some (% of time)"),
             ("io", "victim_io_some_pct", "victim cgroup io.pressure some (% of time)")]
    pairs = [p for p in pairs if p[0] in set(ok["dim"])]
    fig, axes = plt.subplots(1, len(pairs), figsize=(5.4 * len(pairs), 4.4), squeeze=False)
    for j, (dim, col, xlabel) in enumerate(pairs):
        ax = axes[0][j]
        ymin, ymax = 1.0, 1.0
        for arm, st in ARM.items():
            d = ok[(ok["arm"] == arm) & (ok["dim"] == dim)][[col, "p95_norm"]].dropna()
            if d.empty:
                continue
            r = sp[(sp["arm"] == arm) & (sp["dim"] == dim) & (sp["column"] == col)] if not sp.empty else sp
            tag = f"  (Spearman ρ = {r['spearman_rho'].iloc[0]:.2f})" if len(r) else ""
            ax.scatter(d[col], d["p95_norm"], s=22, color=st["color"], marker=st["marker"], alpha=0.7,
                       lw=0, label=st["label"] + tag)
            ymin, ymax = min(ymin, d["p95_norm"].min()), max(ymax, d["p95_norm"].max())
        _log2_axis(ax, ymin, ymax)
        ax.axhline(1, color="0.6", lw=0.8)
        ax.set_xlabel(xlabel)
        ax.set_ylabel("p95 slowdown of this run vs. baseline median")
        ax.set_title(DIM_TITLE[dim], fontsize=11, loc="left")
        ax.set_xlim(left=0)
        ax.grid(color="0.93")
        ax.legend(fontsize=8.5, frameon=False, loc="upper left")
        for s in ("top", "right"):
            ax.spines[s].set_visible(False)
    fig.suptitle("Do host-side stall counters explain the slowdown?  (one point per run, all n)",
                 fontsize=10.5, x=0.01, ha="left")
    _watermark(fig, meta)
    fig.tight_layout(rect=(0, 0.02, 1, 0.95))
    fig.savefig(path, dpi=200)
    plt.close(fig)


def fig_baseline(ok, base, meta, path):
    b0 = ok[ok["n"] == 0].sort_values("ts_start")
    dims = [d for d in ("cpu", "io") if d in set(b0["dim"])]
    if not dims:
        return False
    fig, axes = plt.subplots(2, len(dims), figsize=(5.2 * len(dims), 6.2), squeeze=False)
    for j, dim in enumerate(dims):
        for row, (col, band, label) in enumerate([("p95_norm", GATE["p95_ms"], "p95 / median"),
                                                  ("thr_norm", GATE["throughput"], "throughput / median")]):
            ax = axes[row][j]
            ax.axhspan(1 - band / 100, 1 + band / 100, color="0.9", lw=0)
            for arm, st in ARM.items():
                d = b0[(b0["arm"] == arm) & (b0["dim"] == dim)]
                if d.empty:
                    continue
                bt = base[(base["arm"] == arm) & (base["dim"] == dim)]
                cvv = bt["p95_cv_pct" if col == "p95_norm" else "throughput_cv_pct"].iloc[0]
                ax.plot(range(1, len(d) + 1), d[col], color=st["color"], marker=st["marker"], lw=1.2, ms=6,
                        label=f"{st['label']}  CV {cvv:.1f}%")
            ax.axhline(1, color="0.5", lw=0.8)
            ax.xaxis.set_major_locator(MaxNLocator(integer=True))
            ax.set_ylabel(label)
            ax.legend(fontsize=8.5, frameon=False)
            ax.grid(axis="y", color="0.93")
            if row == 0:
                ax.set_title(f"{DIM_TITLE[dim]}\nshaded = GATE-1 limit ±{band:.0f}%", fontsize=10, loc="left")
            else:
                ax.set_title(f"shaded = GATE-1 limit ±{band:.0f}%", fontsize=9, loc="left")
                ax.set_xlabel("baseline run (n = 0) in time order")
            for s in ("top", "right"):
                ax.spines[s].set_visible(False)
    _watermark(fig, meta)
    fig.tight_layout(rect=(0, 0.02, 1, 1))
    fig.savefig(path, dpi=200)
    plt.close(fig)
    return True


# ---------------------------------------------------------------- summary
def write_summary(out: Path, res, base, sd, dens, at, sp, vv, figs):
    meta, ok, runs = res.meta, res.ok, res.runs
    pf = meta.get("platform", {})
    L = [f"# Results summary — {', '.join(Path(d).name for d in meta['dirs'])}", ""]
    if "SIMULATED" in meta["verdicts"]:
        L += ["> **SIMULATED DATA** — produced by `EXP_SIMULATE=1` to test the pipeline. "
              "Do not report any number from this file.", ""]
    nested = [v for v in meta["verdicts"] if v.startswith("NESTED")]
    if nested:
        L += [f"> **Platform {', '.join(nested)}**: Proxmox ran inside another hypervisor. The VM arm pays "
              "a nested-virtualisation cost the LXC arm does not; treat VM-vs-CT gaps (especially disk I/O) "
              "as an upper bound and say so in the report.", ""]
    if len(meta["verdicts"]) > 1 or len(meta["config_versions"]) > 1:
        L += [f"> **Mixed inputs**: verdicts {meta['verdicts']}, config versions {meta['config_versions']}. "
              "Only pool runs from one platform and one frozen config.", ""]
    failed = runs[runs["ok"] != 1]
    L += [f"- generated: {dt.datetime.now().astimezone().isoformat(timespec='seconds')}",
          f"- platform: `{', '.join(meta['verdicts'])}`; CPU {pf.get('cpu_model', '?')}, "
          f"{pf.get('cpus', '?')} logical CPUs; neighbour cores {pf.get('nbr_cores', '?')}",
          f"- config version(s): {', '.join(meta['config_versions'])}",
          f"- runs: {len(ok)} ok, {len(failed)} failed (failed runs are excluded and listed at the end)", ""]

    L += ["## 1. Baseline and reproducibility gate (RQ1, GATE-1)", "",
          f"GATE-1 passes when, at n = 0, CV(throughput) < {GATE['throughput']:.0f}% and "
          f"CV(p95) < {GATE['p95_ms']:.0f}% with at least 3 runs.", ""]
    if not base.empty:
        L += [md_table(base[["arm", "dim", "runs", "unit", "throughput_median", "throughput_cv_pct",
                             "p95_ms_median", "p95_cv_pct", "gate1"]]), ""]
        for dim in ("cpu", "io"):
            b = base.set_index(["arm", "dim"])
            if ("vm", dim) in b.index and ("ct", dim) in b.index:
                thr = b.loc[("vm", dim), "throughput_median"] / b.loc[("ct", dim), "throughput_median"]
                p95 = b.loc[("vm", dim), "p95_ms_median"] / b.loc[("ct", dim), "p95_ms_median"]
                L.append(f"- {dim}: uncontended VM/CT throughput = {thr:.3f}, p95 = {p95:.3f} "
                         "(virtualisation overhead with no neighbours)")
        L += ["", f"![baseline]({figs.get('baseline', '')})" if figs.get("baseline") else "", ""]
    else:
        L += ["No n = 0 runs yet: run `host/run_matrix.py --label baseline --neighbours 0` first.", ""]

    L += ["## 2. Slowdown curves (ANA-02)", "",
          "S = median p95(n) / median p95(0); R = median throughput(n) / median throughput(0). "
          "[lo, hi] = bootstrap 95% CI; IQR = interquartile range of the per-run ratios. "
          "ρ = (victim cores + n × neighbour cores) / logical CPUs.", ""]
    if not sd.empty and "S" in sd:
        t = sd.copy()
        t["S [95% CI]"] = [f"{fmt(s)} [{fmt(a)}, {fmt(b)}]" for s, a, b in zip(t["S"], t["S_lo"], t["S_hi"])]
        t["S IQR"] = [f"{fmt(a)}–{fmt(b)}" for a, b in zip(t["S_q1"], t["S_q3"])]
        t["R [95% CI]"] = [f"{fmt(s)} [{fmt(a)}, {fmt(b)}]" for s, a, b in zip(t["R"], t["R_lo"], t["R_hi"])]
        L += [md_table(t[["dim", "arm", "intensity", "n", "rho", "runs", "p95_ms", "S [95% CI]", "S IQR",
                          "R [95% CI]"]].sort_values(["dim", "arm", "intensity", "n"])), ""]
        L += [f"![slowdown {k}]({v})" for k, v in figs.items() if k.startswith("slowdown")] + [""]

    L += ["## 3. Consolidation density n* (ANA-03)", "",
          f"n* = smallest number of neighbours at which S ≥ {THRESH:g}, linearly interpolated between measured "
          "points. The interval uses the CI bounds of S (upper bound crosses first, lower bound last).", ""]
    if not dens.empty:
        L += [md_table(dens[["dim", "arm", "intensity", "n_star", "rho_at_n_star", "S_at_max_n", "summary"]]), ""]

    L += ["## 4. Attribution (ANA-04)", "",
          "Median counter rates per condition (% of wall time the victim's tasks were stalled; "
          "for the VM the cgroup is the QEMU process, so its PSI counts vCPU and I/O threads).", ""]
    if not at.empty:
        for dim in ("cpu", "io"):
            cols = ["arm", "intensity", "n", "runs", "S"] + [c for c, _ in METRICS[dim] if c in at]
            sub = at[at["dim"] == dim][cols].sort_values(["arm", "intensity", "n"])
            sub = sub.dropna(axis=1, how="all")
            if not sub.empty:
                L += [f"**{dim}**", "", md_table(sub), ""]
    if not sp.empty:
        t = sp.copy()
        t["p"] = t["p"].map(pval)
        L += ["Spearman rank correlation between each run's p95 slowdown and the counter (all n pooled):", "",
              md_table(t[["dim", "arm", "counter", "runs", "spearman_rho", "p"]]), ""]
    thr_total = ok["victim_throttled_us"].fillna(0).sum() if "victim_throttled_us" in ok else 0
    L += [f"- CFS throttling: total `throttled_usec` over all victim runs = {thr_total:,.0f} µs. "
          + ("As expected without `cpu.max`, contention shows up as queueing (PSI, run-delay), not throttling."
             if thr_total == 0 else "Non-zero: a CPU limit is active on the victim; check the guest config."), ""]
    L += [f"![{k}]({v})" for k, v in figs.items() if k.startswith(("attribution", "explain"))] + [""]

    L += ["## 5. VM vs CT isolation (ANA-05, comparison with Sharma et al. 2016)", "",
          "Per loaded condition: median per-run slowdown for each arm, the CT/VM ratio, and an exact two-sided "
          "permutation test on the difference of medians. `worse_isolation` names an arm only when p < 0.05 "
          "and the gap is at least 10 %. With 5 runs per arm the smallest attainable p is 2/252 ≈ 0.008, "
          "so read p-values as a sanity check, not as proof.", ""]
    if not vv.empty:
        t = vv.copy()
        t["perm_p"] = t["perm_p"].map(pval)
        L += [md_table(t.sort_values(["dim", "intensity", "n"])), ""]
        for dim in ("cpu", "io"):
            d = vv[vv["dim"] == dim]
            if d.empty:
                continue
            top = d.sort_values("n").iloc[-1]
            L.append(f"- {dim}, n = {int(top['n'])}: CT slowdown {top['S_ct']:.2f} vs VM {top['S_vm']:.2f} "
                     f"(CT/VM = {top['ct_over_vm']:.2f}, p = {pval(top['perm_p'])}).")
        L.append("")

    if len(failed):
        L += ["## Failed runs (excluded)", "",
              md_table(failed[["run_id", "note"]].assign(note=failed["note"].str.slice(0, 120))), ""]
    (out / "summary.md").write_text("\n".join(L) + "\n")


# ---------------------------------------------------------------- main
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dirs", nargs="+", help="one or more results/<label> directories")
    ap.add_argument("--out", help="output directory (default: <first dir>/analysis)")
    a = ap.parse_args()

    res = load_results(a.dirs)
    out = Path(a.out) if a.out else Path(a.dirs[0]) / "analysis"
    out.mkdir(parents=True, exist_ok=True)
    ok = res.ok
    if ok.empty:
        raise SystemExit("no ok=1 runs to analyse")

    base = baseline_table(ok)
    ok = normalise(ok, base)
    sd = slowdown_table(ok, res.meta)
    dens = density_table(sd, res.meta)
    at = attribution_table(ok)
    sp = spearman_table(ok)
    vv = vm_vs_ct_table(ok)

    for name, df in [("per_run", ok), ("baseline", base), ("slowdown", sd), ("density", dens),
                     ("attribution", at), ("spearman", sp), ("vm_vs_ct", vv)]:
        df.to_csv(out / f"{name}.csv", index=False)

    figs: dict[str, str] = {}
    if fig_baseline(ok, base, res.meta, out / "fig_baseline.png"):
        figs["baseline"] = "fig_baseline.png"
    if not sd.empty and "S" in sd:
        for inten in sorted(sd["intensity"].unique()):
            fig_slowdown(ok, sd, res.meta, inten, out / f"fig_slowdown_{inten}.png")
            figs[f"slowdown_{inten}"] = f"fig_slowdown_{inten}.png"
            fig_attribution(at, res.meta, inten, out / f"fig_attribution_{inten}.png")
            figs[f"attribution_{inten}"] = f"fig_attribution_{inten}.png"
        fig_explain(ok, sp, res.meta, out / "fig_explain.png")
        figs["explain"] = "fig_explain.png"

    write_summary(out, res, base, sd, dens, at, sp, vv, figs)
    print(f"{len(ok)} ok runs, {len(res.runs) - len(ok)} failed -> {out}/summary.md")
    for f in sorted(figs.values()):
        print(f"  {out / f}")
    if not base.empty and (base["gate1"] == "FAIL").any():
        print("GATE-1: FAIL for " + ", ".join(f"{r.arm}/{r.dim}" for r in base.itertuples() if r.gate1 == "FAIL"))


if __name__ == "__main__":
    main()
