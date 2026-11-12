#!/usr/bin/env python3
"""Numbers for REPORT.md: baseline CV, density n*, pilot comparison, PSI Spearman, cpuset overlap.
usage: python3 analysis/report_stats.py results/<run> results/<pilot> > results/<run>/report_stats.md  (needs pandas, scipy)"""
import sys
import numpy as np, pandas as pd
from scipy.stats import spearmanr

V2, PILOT = sys.argv[1], sys.argv[2]
POLS = ['default', 'float', 'dedicated']; VICS = ['vm', 'ct']; STR = ['cpu', 'mem', 'io']
THR = ['cpu_eps', 'mem_mibs', 'io_iops']; LAT = ['cpu_lat_p99_ms', 'io_p99_us']

def load(d):
    r = pd.read_csv(f'{d}/raw.csv'); r = r[r.metric != 'done']
    w = r.pivot_table(index=['policy', 'rep', 'victim', 'stress', 'n'], columns='metric', values='value').reset_index()
    base = w[w.stress == 'none'].set_index(['policy', 'rep', 'victim'])
    for m in THR + LAT:
        b = w.set_index(['policy', 'rep', 'victim']).index.map(base[m])
        w['rel_' + m] = w[m].values / np.asarray(b, dtype=float)
    return w

w = load(V2); p = load(PILOT)
out = []
def P(s=''): out.append(s)

# 1. baseline CV
P('## 1. Baseline reproducibility (n = 0, 5 reps; CV = sd/mean, sample sd)\n')
P('| policy | victim | ' + ' | '.join(THR + LAT) + ' |'); P('|---' * (2 + len(THR + LAT)) + '|')
for pol in POLS:
    for v in VICS:
        b = w[(w.policy == pol) & (w.victim == v) & (w.stress == 'none')]
        cells = []
        for m in THR + LAT:
            cv = b[m].std(ddof=1) / b[m].mean() * 100
            flag = (m in THR and cv > 5) or (m in LAT and cv > 10)
            cells.append(f"{b[m].mean():.4g} / {cv:.1f}%{' ⚠' if flag else ''}")
        P(f'| {pol} | {v} | ' + ' | '.join(cells) + ' |')
P('\ncell = mean / CV; ⚠ = CV > 5 % (throughput) or > 10 % (p99)\n')

# 2. n*
def cross(ns, ys, thr=2.0):
    for i in range(1, len(ns)):
        if ys[i] >= thr:
            if ys[i - 1] >= thr: return ns[i - 1]
            return ns[i - 1] + (thr - ys[i - 1]) * (ns[i] - ns[i - 1]) / (ys[i] - ys[i - 1])
    return None
P('## 2. Density n* (first n where mean relative p99 ≥ 2, linear interpolation over n = 0,1,2,4,8)\n')
P('Interval: n* of the per-n max curve (earliest) – n* of the per-n min curve (latest) across the 5 reps.\n')
P('| policy | victim | stress | p99 metric | rel p99 at n=1,2,4,8 (mean) | n* | interval |'); P('|---|---|---|---|---|---|---|')
nstar = []
for pol in POLS:
    for v in VICS:
        for s in STR:
            for m in LAT:
                d = w[(w.policy == pol) & (w.victim == v) & (((w.stress == s) & (w.n > 0)) | (w.stress == 'none'))]
                g = d.groupby('n')['rel_' + m]; ns = list(g.mean().index.astype(int))
                mean, mn, mx = g.mean().values, g.min().values, g.max().values
                c, lo, hi = cross(ns, mean), cross(ns, mx), cross(ns, mn)
                f = lambda x: '—' if x is None else f'{x:.2f}'
                cs = f'n = 8 still < 2 ({mean[-1]:.2f})' if c is None else (f'≤ 1 (interp. {c:.2f})' if c < 1 else f'{c:.2f}')
                iv = f"{f(lo)} – {f(hi) if hi is not None else '>8'}" if lo is not None else '—'
                P(f"| {pol} | {v} | {s} | {m} | {', '.join(f'{x:.2f}' for x in mean[1:])} | {cs} | {iv} |")
                nstar.append((pol, v, s, m, c, lo, hi))
P()

# 3. pilot comparison
P('## 3. Pilot comparison: v2 mean relative value inside pilot min–max (n = 2, 4, 8)\n')
P('| metric | inside | total | share |'); P('|---|---|---|---|')
rows = []
for m in THR + LAT:
    ins = tot = 0
    for pol in POLS:
        for v in VICS:
            for s in STR:
                for n in [2, 4, 8]:
                    a = w[(w.policy == pol) & (w.victim == v) & (w.stress == s) & (w.n == n)]['rel_' + m]
                    b = p[(p.policy == pol) & (p.victim == v) & (p.stress == s) & (p.n == n)]['rel_' + m]
                    if len(a) == 0 or len(b) == 0: continue
                    ok = b.min() <= a.mean() <= b.max(); ins += ok; tot += 1
                    rows.append((m, pol, v, s, n, a.mean(), b.min(), b.max(), ok))
    P(f'| {m} | {ins} | {tot} | {ins / tot:.0%} |')
P('\nOutside the pilot range by more than 0.05 (absolute, relative units):\n')
P('| metric | policy | victim | stress | n | v2 mean | pilot min–max |'); P('|---|---|---|---|---|---|---|')
for m, pol, v, s, n, a, lo, hi, ok in rows:
    if not ok and (a < lo - .05 or a > hi + .05):
        P(f'| {m} | {pol} | {v} | {s} | {n} | {a:.2f} | {lo:.2f}–{hi:.2f} |')
P()

# 4. PSI
P('## 4. CPU PSI vs CPU throughput (Spearman ρ; CPU stress + baselines)\n')
P('| subset | victim | N | ρ | p |'); P('|---|---|---|---|---|')
d = w[w.stress.isin(['cpu', 'none'])]
for sub in ['all'] + POLS:
    for v in VICS:
        e = d[(d.victim == v) & ((d.policy == sub) if sub != 'all' else True)]
        r = spearmanr(e.cpu_psi_pct, e.rel_cpu_eps)
        P(f'| {sub} | {v} | {len(e)} | {r.statistic:.2f} | {r.pvalue:.1g} |')
dd = d[(d.policy == 'dedicated') & (d.n == 8) & (d.stress == 'cpu')]
P(f"\ndedicated, CPU stress, n = 8: rel throughput vm {dd[dd.victim=='vm'].rel_cpu_eps.mean():.3f} / ct {dd[dd.victim=='ct'].rel_cpu_eps.mean():.3f}; "
  f"cpu PSI vm {dd[dd.victim=='vm'].cpu_psi_pct.mean():.2f} % / ct {dd[dd.victim=='ct'].cpu_psi_pct.mean():.2f} %; host cpu PSI {dd.host_cpu_psi_pct.mean():.1f} %")
io = w[(w.stress == 'io') & (w.n == 8)]
P('\nIO PSI under disk stress, n = 8 (mean %): ' + ', '.join(f"{pol}/{v} {io[(io.policy==pol)&(io.victim==v)].io_psi_pct.mean():.1f}" for pol in POLS for v in VICS))
P()

# 5. cpuset overlap (policy = default)
P('## 5. Policy default: CPU throughput vs number of victim CPUs shared with busy neighbours (overlap_max)\n')
o = w[(w.policy == 'default') & w.stress.isin(['cpu', 'mem']) & (w.n > 0)]
P(o.groupby(['victim', 'stress', 'overlap_max']).agg(N=('rel_cpu_eps', 'size'), thr_mean=('rel_cpu_eps', 'mean'), thr_min=('rel_cpu_eps', 'min'),
  thr_max=('rel_cpu_eps', 'max'), p99_mean=('rel_cpu_lat_p99_ms', 'mean')).round(2).to_string() + '\n')

# extra numbers for the report
P('## 6. Extra numbers\n')
b = w[w.stress == 'none'].groupby(['policy', 'victim'])[['io_iops', 'cpu_eps', 'mem_mibs']].mean()
P('Baseline means by policy:\n\n' + b.round(1).to_string() + '\n')
t = w[w.n > 0].groupby(['policy', 'stress', 'victim', 'n'])[['rel_cpu_eps', 'rel_cpu_lat_p99_ms', 'rel_mem_mibs', 'rel_io_iops', 'rel_io_p99_us']].mean().round(3)
P('Mean relative values (all n):\n\n' + t.to_string() + '\n')
post = w[w.stress == 'post'].groupby(['policy', 'victim'])[['rel_cpu_eps', 'rel_io_iops']].agg(['mean', 'min', 'max']).round(2)
P('Drift check (post / baseline):\n\n' + post.to_string() + '\n')
P('Memory bandwidth under memory stress, n = 8 (rel mean / min / max):\n')
P(w[(w.stress == 'mem') & (w.n == 8)].groupby(['policy', 'victim']).rel_mem_mibs.agg(['mean', 'min', 'max']).round(2).to_string() + '\n')
P('Disk under disk stress, n = 8 (absolute means, relative IOPS):\n')
P(w[(w.stress == 'io') & (w.n == 8)].groupby(['policy', 'victim'])[['io_iops', 'io_p99_us', 'rel_io_iops']].mean().round(2).to_string() + '\n')
P('VM disk IOPS under CPU stress (rel mean / min / max by n):\n')
P(w[(w.victim == 'vm') & (w.stress == 'cpu') & (w.n > 0)].groupby(['policy', 'n']).rel_io_iops.agg(['mean', 'min', 'max']).round(2).to_string() + '\n')
print('\n'.join(out))
