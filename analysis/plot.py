import os
_here = os.path.dirname(os.path.abspath(__file__))
exec(open(os.path.join(_here, 'load.py')).read())
# usage: plot.py                         -> pilot data, figures written next to this script
#        plot.py results/<dir> ["note"]  -> that run, figures written into results/<dir>/
os.chdir(DATA.parent if len(sys.argv) > 1 else _here)
import matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot as plt
from matplotlib.ticker import FixedLocator, FuncFormatter, NullLocator
plt.rcParams.update({'font.size':10,'axes.spines.top':False,'axes.spines.right':False,'axes.edgecolor':'#8a8984','axes.labelcolor':'#52514e','xtick.color':'#52514e','ytick.color':'#52514e'})
C={'vm':'#2a78d6','ct':'#eb6834'}; M={'vm':'o','ct':'s'}; LAB={'vm':'KVM VM','ct':'LXC CT'}
POL=[('default','default (Proxmox defaults)'),('float','float (cpulimit, no cpuset)'),('dedicated','dedicated (disjoint cpusets)')]
L=w[~w.stress.isin(['post'])].copy()
NS=sorted({0,*L.n.astype(int).unique()})   # neighbour counts present in the data (was hard-coded [0,2,4,8])
def series(pol,v,stress,col):
    rows=[]
    for n in NS:
        d=L[(L.policy==pol)&(L.victim==v)&(((L.stress==stress)&(L.n==n))|((L.stress=='none')&(n==0)))]
        x=d[col]; rows.append((n,x.mean(),x.min(),x.max()))
    return np.array(rows)
note=(sys.argv[2] if len(sys.argv) > 2 else DATA.parent.name if len(sys.argv) > 1 else
      "Proxmox VE 9.2 nested in VMware Workstation (i5-14400, 12 vCPU) · 3 reps × 15 s · pilot data, not report-grade") + " · line = mean, band = min–max"
# fig1 cpu
fig,ax=plt.subplots(2,3,figsize=(12,6.6),sharex=True)
for j,(pol,title) in enumerate(POL):
    for v in ['vm','ct']:
        for i,(col,log) in enumerate([('rel_cpu_eps',False),('rel_cpu_lat_p99_ms',True)]):
            a=series(pol,v,'cpu',col); x=np.arange(len(NS))+(-0.06 if v=='vm' else 0.06)
            ax[i][j].fill_between(x,a[:,2],a[:,3],color=C[v],alpha=.15,lw=0)
            ax[i][j].plot(x,a[:,1],color=C[v],marker=M[v],lw=2,ms=6,label=LAB[v])
    ax[0][j].set_title(title,loc='left',fontsize=10.5)
    ax[0][j].set_ylim(0,1.1); ax[0][j].axhline(1,color='#c3c2b7',lw=.8)
    ax[1][j].set_yscale('log',base=2); ax[1][j].set_ylim(0.8,32)
    ax[1][j].yaxis.set_major_locator(FixedLocator([1,2,4,8,16,32])); ax[1][j].yaxis.set_minor_locator(NullLocator())
    ax[1][j].yaxis.set_major_formatter(FuncFormatter(lambda v,_:f'×{v:g}'))
    ax[1][j].axhline(2,color='#52514e',ls='--',lw=.9); ax[1][j].axhline(1,color='#c3c2b7',lw=.8)
    ax[1][j].set_xticks(range(len(NS))); ax[1][j].set_xticklabels([str(n) for n in NS]); ax[1][j].set_xlabel('noisy neighbours n (LXC, 2 cores, CPU stress)')
    for i in range(2): ax[i][j].grid(axis='y',color='#ecebe8')
ax[0][0].set_ylabel('CPU throughput vs baseline'); ax[1][0].set_ylabel('CPU p99 latency vs baseline')
ax[0][0].legend(frameon=False,loc='lower left')
fig.suptitle('CPU contention: the container suffers only when its CPUs are shared with neighbours',x=.01,ha='left',fontsize=12)
fig.text(.01,.005,note,fontsize=7.5,color='#8a8984'); fig.tight_layout(rect=(0,.02,1,.96)); fig.savefig('fig1_cpu.png',dpi=200)

# fig2 mem + io
fig,ax=plt.subplots(1,3,figsize=(12,3.9))
for v in ['vm','ct']:
    for k,(stress,col,lab) in enumerate([('mem','rel_mem_mibs','memory bandwidth vs baseline'),('io','io_iops','random-read IOPS (absolute)'),('io','io_p99_us','read p99 latency (µs, absolute)')]):
        pol='default'
        a=series(pol,v,stress,col); x=np.arange(len(NS))+(-0.06 if v=='vm' else 0.06)
        ax[k].fill_between(x,a[:,2],a[:,3],color=C[v],alpha=.15,lw=0)
        ax[k].plot(x,a[:,1],color=C[v],marker=M[v],lw=2,ms=6,label=LAB[v])
        ax[k].set_ylabel(lab); ax[k].set_xticks(range(len(NS))); ax[k].set_xticklabels([str(n) for n in NS])
        ax[k].set_xlabel(f'noisy neighbours n ({stress} stress)'); ax[k].grid(axis='y',color='#ecebe8'); ax[k].set_ylim(bottom=0)
ax[0].set_ylim(0,1.1); ax[0].legend(frameon=False,loc='lower left')
ax[1].yaxis.set_major_formatter(FuncFormatter(lambda v,_:f'{v/1000:g}k'))
fig.suptitle('Memory bandwidth and disk: VM and CT lose the same amount (policy = default; other policies look the same)',x=.01,ha='left',fontsize=12)
fig.text(.01,.005,note,fontsize=7.5,color='#8a8984'); fig.tight_layout(rect=(0,.03,1,.93)); fig.savefig('fig2_mem_io.png',dpi=200)

# fig3 psi
fig,ax=plt.subplots(figsize=(6.4,4.4))
d=L[L.stress.isin(['cpu','none'])]
from scipy.stats import spearmanr
for v in ['vm','ct']:
    e=d[d.victim==v]; r=spearmanr(e.cpu_psi_pct,e.rel_cpu_eps).statistic
    ax.scatter(e.cpu_psi_pct,e.rel_cpu_eps,s=30,color=C[v],marker=M[v],alpha=.75,lw=0,label=f'{LAB[v]} (Spearman ρ = {r:.2f})')
ax.set_xlabel("victim cgroup cpu.pressure 'some' (% of wall time)"); ax.set_ylabel('CPU throughput vs baseline')
ax.set_ylim(0,1.1); ax.set_xlim(-2,100); ax.grid(color='#ecebe8'); ax.legend(frameon=False,loc='lower left',fontsize=9)
ax.set_title('PSI tracks the container collapse, not the common ~15% loss',loc='left',fontsize=11)
fig.text(.01,.005,'all policies, CPU stress + baselines',fontsize=7.5,color='#8a8984'); fig.tight_layout(rect=(0,.02,1,1)); fig.savefig('fig3_psi.png',dpi=200)
