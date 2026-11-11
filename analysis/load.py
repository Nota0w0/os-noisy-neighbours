import sys
from pathlib import Path
import pandas as pd, numpy as np
DATA = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent / 'results' / 'nested-2026-10-19' / 'raw.csv'
r=pd.read_csv(DATA)
r=r[r.metric!='done']
w=r.pivot_table(index=['policy','rep','victim','stress','n'],columns='metric',values='value').reset_index()
base=w[w.stress=='none'].set_index(['policy','rep','victim'])
mets=['cpu_eps','cpu_lat_p99_ms','mem_mibs','io_iops','io_p99_us','wake_p50_us','wake_p99_us','wake_p999_us','cpu_psi_pct','io_psi_pct','host_cpu_psi_pct']
for m in mets:
    w['rel_'+m]=[row[m]/base.loc[(row.policy,row.rep,row.victim),m] if base.loc[(row.policy,row.rep,row.victim),m] else np.nan for _,row in w.iterrows()]
