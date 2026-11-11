#!/usr/bin/env bash
# osproj.sh (final) — Performance isolation under contention: KVM VM vs LXC on Proxmox VE
# Touches ONLY guests: victim-vm(9100) victim-ct(9101) nbr1..nbr8(9111..9118)
# Usage: bash osproj.sh all | status | summary | clean
#   all     : build/check guests (idempotent) -> preflight of every policy -> full matrix
#             (runs in background, resumable: re-run 'all' to continue after an interruption)
#   status  : progress
#   summary : recompute results_final/summary.csv
#   clean   : destroy ONLY the 10 project guests
set -u
SELF=$(readlink -f "$0")
W=${W:-/root/osproj}; R=$W/results_final; LOG=$W/run_final.log
CG=${CG:-/sys/fs/cgroup}; PVECONF=${PVECONF:-/etc/pve/lxc}; QPID=${QPID:-/var/run/qemu-server}; HOSTPSI=${HOSTPSI:-/proc/pressure}
mkdir -p "$W"

# ---------------- parameters ----------------
VMID_VM=9100; VMID_CT=9101; NBR_BASE=9110          # nbrK -> 9110+K
STORE=local-lvm
VICT_CORES=2; VICT_MEM=2048; VICT_DISK=16
NBR_CORES=2;  NBR_MEM=1024;  NBR_DISK=8
REPS=${REPS:-3}                                    # repetitions (each rep = every policy x both victims)
BT=${BT:-15}                                       # seconds per victim benchmark
NLIST=${NLIST:-"2 4 8"}                            # neighbour counts
STRESSES=${STRESSES:-"cpu mem io"}
POLICIES=${POLICIES:-"default float dedicated"}    # CPU placement policies (see below)
PREFLIGHT=${PREFLIGHT:-1}
PKGS_V="sysbench fio stress-ng python3-minimal"
PKGS_N="fio stress-ng"
NCPU=${NCPU:-$(nproc)}
DED=${DED:-"$((NCPU-2))-$((NCPU-1))"}              # victim CPUs under 'dedicated'
SHARED=${SHARED:-"0-$((NCPU-3))"}                  # neighbour CPUs under 'dedicated'

PVE_IP=$(ip -4 -br addr show vmbr0 2>/dev/null | awk '{print $3}' | cut -d/ -f1)
PFX=${PVE_IP%.*}
GW=$(ip route 2>/dev/null | awk '/^default/{print $3; exit}')
DNS=$(awk '/^nameserver/{print $2; exit}' /etc/resolv.conf)
IP_VM=$PFX.60; IP_CT=$PFX.61
nbr_id(){ echo $((NBR_BASE+$1)); }
nbr_ip(){ echo "$PFX.$((70+$1))"; }
NBR_IDS=""; for _k in 1 2 3 4 5 6 7 8; do NBR_IDS="$NBR_IDS $((NBR_BASE+_k))"; done
KEY=$W/id_ed25519
SSHO="-n -i $KEY -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -o ConnectTimeout=5 -o BatchMode=yes"

log(){ echo "[$(date '+%F %T')] $*"; }
die(){ log "ERROR: $*"; exit 1; }

# wake-up latency probe (no privileges needed; identical code in both victims)
WAKE_B64=$(base64 -w0 <<'PY'
import time,sys
D=float(sys.argv[1]); end=time.perf_counter()+D; o=[]
pc=time.perf_counter_ns; sl=time.sleep
while time.perf_counter()<end:
    t=pc(); sl(0.001); o.append((pc()-t)/1000.0-1000.0)
o.sort(); n=len(o)
q=lambda p:o[min(n-1,int(n*p))]
print("WAKE %.1f %.1f %.1f %.1f %d"%(q(.5),q(.99),q(.999),o[-1],n))
PY
)

# ---------------- safety guard ----------------
# own ID NAME KIND : 0 = ID free, 1 = exists and is ours; dies if ID belongs to anything else
own(){
  local id=$1 name=$2 kind=$3 cur
  if qm config "$id" >/dev/null 2>&1; then
    [ "$kind" = vm ] || die "VMID $id is a VM, expected CT '$name' — refusing to touch"
    cur=$(qm config "$id" | awk '/^name:/{print $2}')
  elif pct config "$id" >/dev/null 2>&1; then
    [ "$kind" = ct ] || die "VMID $id is a CT, expected VM '$name' — refusing to touch"
    cur=$(pct config "$id" | awk '/^hostname:/{print $2}')
  else
    return 0
  fi
  [ "$cur" = "$name" ] || die "VMID $id belongs to '$cur', not '$name' — refusing to touch"
  return 1
}
ip_free(){ ping -c1 -W1 "$1" >/dev/null 2>&1 && die "IP $1 already in use"; return 0; }
check_all_ours(){
  local k
  own "$VMID_VM" victim-vm vm && die "victim-vm missing"
  own "$VMID_CT" victim-ct ct && die "victim-ct missing"
  for k in $(seq 8); do own "$(nbr_id "$k")" "nbr$k" ct && die "nbr$k missing"; done
  return 0
}

# ---------------- guest helpers ----------------
ct_running(){ [ "$(pct status "$1" 2>/dev/null | awk '{print $2}')" = running ]; }
vm_running(){ [ "$(qm status "$1" 2>/dev/null | awk '{print $2}')" = running ]; }
ct_stop(){ ct_running "$1" || return 0; pct shutdown "$1" --timeout 30 >/dev/null 2>&1; ct_running "$1" && pct stop "$1" >/dev/null 2>&1; return 0; }

ct_ready(){
  local id=$1 i
  for i in $(seq 60); do
    pct exec "$id" -- getent hosts deb.debian.org >/dev/null 2>&1 && return 0
    sleep 2
  done
  die "CT $id network not ready"
}
vm_ready(){
  local i
  for i in $(seq 120); do
    ssh $SSHO debian@"$IP_VM" true 2>/dev/null && {
      ssh $SSHO debian@"$IP_VM" 'sudo cloud-init status --wait >/dev/null 2>&1; true'
      return 0; }
    sleep 3
  done
  die "victim-vm SSH not ready"
}

vexec(){  # vexec vm|ct "command"
  local v=$1; shift
  if [ "$v" = vm ]; then ssh $SSHO debian@"$IP_VM" "sudo bash -c $(printf %q "$*")"
  else pct exec "$VMID_CT" -- bash -c "$*" </dev/null; fi
}
install_cmd(){ echo "dpkg -s $1 >/dev/null 2>&1 || (apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq $1 >/dev/null)"; }
prep_cmd(){ echo "[ -s $1 ] || fio --name=prep --filename=$1 --size=1G --rw=write --bs=1M --direct=1 --ioengine=libaio >/dev/null 2>&1"; }
wake_cmd(){ echo "echo $WAKE_B64 | base64 -d > /root/wake.py"; }

get_tmpl(){
  local t n bad
  for bad in $(pveam list local 2>/dev/null | awk '{print $1}' | grep -E 'debian-1[23]-standard_.*_arm64'); do
    log "removing wrong-arch template $bad" >&2; pveam remove "$bad" >/dev/null 2>&1
  done
  t=$(pveam list local 2>/dev/null | awk '{print $1}' | grep -E 'debian-1[23]-standard_.*_amd64' | sort -V | tail -1)
  if [ -z "$t" ]; then
    pveam update >/dev/null 2>&1
    n=$(pveam available --section system | awk '{print $2}' | grep -E '^debian-13-standard_.*_amd64' | sort -V | tail -1)
    [ -z "$n" ] && n=$(pveam available --section system | awk '{print $2}' | grep -E '^debian-12-standard_.*_amd64' | sort -V | tail -1)
    [ -z "$n" ] && die "no Debian LXC template available"
    pveam download local "$n" >&2 || die "template download failed"
    t="local:vztmpl/$n"
  fi
  echo "$t"
}

mk_ct(){  # id name ip cores mem disk tmpl
  local id=$1 name=$2 ip=$3 c=$4 m=$5 d=$6 t=$7
  if ! own "$id" "$name" ct && ! pct config "$id" | grep -q '^arch: amd64'; then
    log "CT $id $name has wrong arch — rebuilding"
    pct stop "$id" >/dev/null 2>&1; pct destroy "$id" --purge >/dev/null || die "destroy $name"
  fi
  if own "$id" "$name" ct; then
    ip_free "$ip"
    log "create CT $id $name ($ip)"
    pct create "$id" "$t" --hostname "$name" --cores "$c" --memory "$m" --swap 0 \
      --rootfs "$STORE:$d" --net0 "name=eth0,bridge=vmbr0,ip=$ip/24,gw=$GW" \
      --nameserver "$DNS" --unprivileged 1 --features nesting=1 \
      --ssh-public-keys "$KEY.pub" --onboot 0 >/dev/null || die "pct create $name"
  fi
  ct_running "$id" || pct start "$id" || die "pct start $name"
}

mk_vm(){
  local img=$W/debian-cloud.qcow2
  if own "$VMID_VM" victim-vm vm; then
    ip_free "$IP_VM"
    if [ ! -s "$img" ]; then
      log "download Debian cloud image"
      wget -q -O "$img.part" https://cloud.debian.org/images/cloud/trixie/latest/debian-13-genericcloud-amd64.qcow2 \
        || wget -q -O "$img.part" https://cloud.debian.org/images/cloud/bookworm/latest/debian-12-genericcloud-amd64.qcow2 \
        || die "cloud image download failed"
      mv "$img.part" "$img"
    fi
    log "create VM $VMID_VM victim-vm ($IP_VM)"
    qm create "$VMID_VM" --name victim-vm --memory "$VICT_MEM" --balloon 0 --cores "$VICT_CORES" --cpu host \
      --net0 virtio,bridge=vmbr0 --scsihw virtio-scsi-single --ostype l26 --serial0 socket --vga serial0 \
      --scsi0 "$STORE:0,import-from=$img,iothread=1,cache=none" --ide2 "$STORE:cloudinit" --boot order=scsi0 \
      --ciuser debian --sshkeys "$KEY.pub" --ipconfig0 "ip=$IP_VM/24,gw=$GW" --nameserver "$DNS" \
      >/dev/null || die "qm create victim-vm"
    qm resize "$VMID_VM" scsi0 "${VICT_DISK}G" >/dev/null || die "qm resize"
  fi
}

activate(){  # activate vm|ct  (only one victim running at a time)
  if [ "$1" = vm ]; then
    ct_stop "$VMID_CT"
    vm_running "$VMID_VM" || qm start "$VMID_VM" >/dev/null || die "qm start victim-vm"
    vm_ready
  else
    vm_running "$VMID_VM" && { qm shutdown "$VMID_VM" --timeout 90 >/dev/null 2>&1 || qm stop "$VMID_VM" >/dev/null 2>&1; }
    ct_running "$VMID_CT" || pct start "$VMID_CT" || die "pct start victim-ct"
    ct_ready "$VMID_CT"
  fi
}
deactivate_all(){
  vm_running "$VMID_VM" && { qm shutdown "$VMID_VM" --timeout 90 >/dev/null 2>&1 || qm stop "$VMID_VM" >/dev/null 2>&1; }
  ct_stop "$VMID_CT"
  return 0
}

# ---------------- build (idempotent) ----------------
do_build(){
  log "== BUILD/CHECK  (pve=$PVE_IP gw=$GW dns=$DNS ncpu=$NCPU ded=$DED shared=$SHARED)"
  [ -s "$KEY" ] || ssh-keygen -q -t ed25519 -N '' -f "$KEY" -C osproj
  local t k id; t=$(get_tmpl); log "template: $t"

  for k in $(seq 8); do mk_ct "$(nbr_id "$k")" "nbr$k" "$(nbr_ip "$k")" "$NBR_CORES" "$NBR_MEM" "$NBR_DISK" "$t"; done
  for k in $(seq 8); do
    id=$(nbr_id "$k"); ct_ready "$id"
    pct exec "$id" -- bash -c "$(install_cmd "$PKGS_N"); $(prep_cmd /root/n.dat)" </dev/null || die "setup nbr$k"
  done
  log "nbr1-8 ready"

  mk_ct "$VMID_CT" victim-ct "$IP_CT" "$VICT_CORES" "$VICT_MEM" "$VICT_DISK" "$t"
  ct_ready "$VMID_CT"
  vexec ct "$(install_cmd "$PKGS_V"); $(prep_cmd /root/fio.dat); $(wake_cmd)" || die "setup victim-ct"
  vexec ct "python3 /root/wake.py 1" | grep -q '^WAKE' || die "wake probe failed in victim-ct"
  log "victim-ct ready"

  mk_vm; activate vm
  vexec vm "$(install_cmd "$PKGS_V"); $(prep_cmd /root/fio.dat); $(wake_cmd)" || die "setup victim-vm"
  vexec vm "python3 /root/wake.py 1" | grep -q '^WAKE' || die "wake probe failed in victim-vm"
  log "victim-vm ready"

  { echo "== params"; echo "REPS=$REPS BT=$BT NLIST=$NLIST STRESSES=$STRESSES POLICIES=$POLICIES"
    echo "NCPU=$NCPU DED=$DED SHARED=$SHARED"
    echo "victim cores=$VICT_CORES mem=$VICT_MEM disk=$VICT_DISK ; nbr cores=$NBR_CORES mem=$NBR_MEM disk=$NBR_DISK"
    echo "== pve"; pveversion; grep -m1 'model name' /proc/cpuinfo; free -h | head -2; echo "host kernel (= victim-ct kernel): $(uname -r)"
    echo "== cpu topology (lscpu -e)"; lscpu -e 2>/dev/null
    echo "== victim-vm"; qm config "$VMID_VM"
    echo "== victim-vm kernel"; vexec vm "uname -r"
    echo "== victim-ct"; pct config "$VMID_CT"
    echo "== nbr1"; pct config "$(nbr_id 1)"
  } > "$R/meta.txt" 2>&1
  log "== BUILD done"
}

# ---------------- CPU placement ----------------
cpulist(){ local p; for p in ${1//,/ }; do if [[ $p == *-* ]]; then seq "${p%-*}" "${p#*-}"; else echo "$p"; fi; done; }
cpus_eq(){ [ "$(cpulist "$1" | sort -n | tr '\n' ,)" = "$(cpulist "$2" | sort -n | tr '\n' ,)" ]; }
ct_cpus(){ cat "$CG/lxc/$1/ns/cpuset.cpus.effective" 2>/dev/null || cat "$CG/lxc/$1/cpuset.cpus.effective" 2>/dev/null || echo NA; }
vm_pid(){ cat "$QPID/$VMID_VM.pid" 2>/dev/null; }
vm_cpus(){ taskset -pc "$(vm_pid)" 2>/dev/null | awk -F': ' '{print $2}'; }
victim_cpus(){ if [ "$1" = vm ]; then vm_cpus; else ct_cpus "$VMID_CT"; fi; }
nbr_cpus(){ local k s=""; for k in $(seq 8); do s="$s nbr$k=$(ct_cpus "$(nbr_id "$k")")"; done; echo "${s# }"; }
overlap(){  # v n -> number of victim CPUs also usable by the n active neighbours
  local v=$1 n=$2 k nb=""
  [ "$n" -gt 0 ] || { echo 0; return; }
  for k in $(seq "$n"); do nb="$nb $(cpulist "$(ct_cpus "$(nbr_id "$k")")")"; done
  comm -12 <(cpulist "$(victim_cpus "$v")" | sort -u) <(printf '%s\n' $nb | sort -u) | wc -l
}
# Fixed cpuset written into the CT config: pvestatd skips CTs that carry this key when it
# periodically rebalances cpusets, and LXC applies it at start. CT must be stopped.
cfg_cpuset(){  # id cpus|""
  local f=$PVECONF/$1.conf tmp
  ct_running "$1" && die "cfg_cpuset: CT $1 must be stopped"
  [ -f "$f" ] || die "config $f not found"
  grep -q '^\[' "$f" && die "CT $1 has snapshot/pending sections — refusing to edit $f by hand"
  tmp=$(mktemp)
  grep -v '^lxc\.cgroup2\.cpuset\.cpus' "$f" > "$tmp"
  [ -n "$2" ] && echo "lxc.cgroup2.cpuset.cpus: $2" >> "$tmp"
  cat "$tmp" > "$f" || die "cannot write $f"; rm -f "$tmp"
  if [ -n "$2" ]; then grep -qx "lxc.cgroup2.cpuset.cpus: $2" "$f" || die "cpuset key not persisted in $f"
  else grep -q '^lxc\.cgroup2\.cpuset\.cpus' "$f" && die "cpuset key not removed from $f"; fi
  return 0
}
verify_ct_cpus(){ local eff; eff=$(ct_cpus "$1"); cpus_eq "$eff" "$2" || die "placement check CT $1: effective=$eff expected=$2"; }

# default   : Proxmox defaults. CT 'cores' -> cpuset chosen and periodically rebalanced by pvestatd;
#             VM vCPU threads float over all host CPUs.
# float     : no pinning anywhere; CTs capped by CPU quota (cpulimit) instead of a cpuset.
# dedicated : victim on DED only, neighbours on SHARED only (no CPU sharing with the victim).
apply_policy(){
  local pol=$1 id c
  log "== POLICY $pol"
  deactivate_all
  for id in $NBR_IDS; do ct_stop "$id"; done
  for id in $NBR_IDS $VMID_CT; do
    c=$NBR_CORES; [ "$id" = "$VMID_CT" ] && c=$VICT_CORES
    case $pol in
      default)
        pct set "$id" --cores "$c" >/dev/null || die "pct set cores $id"
        if pct config "$id" | grep -q '^cpulimit:'; then pct set "$id" --delete cpulimit >/dev/null || die "pct delete cpulimit $id"; fi
        cfg_cpuset "$id" "" ;;
      float|dedicated)
        pct set "$id" --cpulimit "$c" >/dev/null || die "pct set cpulimit $id"
        if pct config "$id" | grep -q '^cores:'; then pct set "$id" --delete cores >/dev/null || die "pct delete cores $id"; fi
        if [ "$pol" = dedicated ]; then
          if [ "$id" = "$VMID_CT" ]; then cfg_cpuset "$id" "$DED"; else cfg_cpuset "$id" "$SHARED"; fi
        else cfg_cpuset "$id" ""; fi ;;
      *) die "unknown policy $pol" ;;
    esac
  done
  if [ "$pol" = dedicated ]; then
    qm set "$VMID_VM" --affinity "$DED" >/dev/null || die "qm set affinity"
  elif qm config "$VMID_VM" | grep -q '^affinity:'; then
    qm set "$VMID_VM" --delete affinity >/dev/null || die "qm delete affinity"
  fi
  for id in $NBR_IDS; do pct start "$id" || die "pct start $id"; done
  sleep 5
  if [ "$pol" = dedicated ]; then for id in $NBR_IDS; do verify_ct_cpus "$id" "$SHARED"; done; fi
  return 0
}
enforce_victim(){  # pol v — verify placement after every victim (re)start
  if [ "$1" = dedicated ]; then
    if [ "$2" = ct ]; then verify_ct_cpus "$VMID_CT" "$DED"
    else cpus_eq "$(vm_cpus)" "$DED" || die "placement check victim-vm: affinity=$(vm_cpus) expected=$DED"; fi
  fi
  return 0
}

# ---------------- PSI telemetry ----------------
vcgroup(){
  if [ "$1" = vm ]; then echo "$CG$(awk -F: '$1=="0"{print $3}' "/proc/$(vm_pid)/cgroup" 2>/dev/null)"
  else echo "$CG/lxc/$VMID_CT"; fi
}
psi(){ awk '/^some/{for(i=1;i<=NF;i++) if($i ~ /^total=/){sub(/total=/,"",$i); print $i}}' "$1" 2>/dev/null; }
psi_pct(){ [ -n "$1" ] && [ -n "$2" ] && awk -v a="$1" -v b="$2" -v t0="$3" -v t1="$4" 'BEGIN{printf "%.2f",(b-a)/((t1-t0)/1000)*100}'; }

# ---------------- noise ----------------
stress_cmd(){  # type seconds
  case $1 in
    cpu) echo "stress-ng --cpu $NBR_CORES --cpu-method matrixprod --timeout ${2}s" ;;
    mem) echo "stress-ng --stream $NBR_CORES --timeout ${2}s" ;;
    io)  echo "fio --name=n --filename=/root/n.dat --size=1G --rw=randrw --bs=4k --iodepth=32 --ioengine=libaio --direct=1 --runtime=$2 --time_based" ;;
  esac
}
PIDS=()
start_noise(){  # type n
  local k T=$((5*BT+90)); PIDS=()
  for k in $(seq "$2"); do
    pct exec "$(nbr_id "$k")" -- bash -c "$(stress_cmd "$1" "$T")" </dev/null >/dev/null 2>&1 &
    PIDS+=($!)
  done
  sleep 5
}
stop_noise(){
  local k
  for k in $(seq 8); do pct exec "$(nbr_id "$k")" -- bash -c "pkill -x stress-ng; pkill -x fio; true" </dev/null >/dev/null 2>&1; done
  [ ${#PIDS[@]} -gt 0 ] && wait "${PIDS[@]}" 2>/dev/null
  PIDS=(); sleep 5
}

# ---------------- benchmarks ----------------
rec(){ echo "$(date +%s),$1,$2,${3:-NA}" >> "$R/raw.csv"; }
bench(){  # v tag(pol,rep,v,stress,n)
  local v=$1 tag=$2 n=${2##*,} d cg p0 p1 h0 h1 t0 t1 iops p99 spid
  d=$R/raw/${tag//,/_}; mkdir -p "$d"; cg=$(vcgroup "$v")
  : > "$d/place.txt"
  ( while :; do printf '%s\t%s\t%s\n' "$(overlap "$v" "$n")" "$(victim_cpus "$v")" "$(nbr_cpus)" >> "$d/place.txt"; sleep 2; done ) &
  spid=$!

  p0=$(psi "$cg/cpu.pressure"); h0=$(psi "$HOSTPSI/cpu"); t0=$(date +%s%N)
  vexec "$v" "sysbench cpu --threads=$VICT_CORES --time=$BT --percentile=99 run" > "$d/cpu.txt" 2>&1
  p1=$(psi "$cg/cpu.pressure"); h1=$(psi "$HOSTPSI/cpu"); t1=$(date +%s%N)
  rec "$tag" cpu_eps        "$(awk '/events per second/{print $4}' "$d/cpu.txt")"
  rec "$tag" cpu_lat_p99_ms "$(awk '/99th percentile/{print $3}' "$d/cpu.txt")"
  rec "$tag" cpu_psi_pct    "$(psi_pct "$p0" "$p1" "$t0" "$t1")"
  rec "$tag" host_cpu_psi_pct "$(psi_pct "$h0" "$h1" "$t0" "$t1")"

  vexec "$v" "sysbench memory --threads=$VICT_CORES --memory-block-size=1M --memory-total-size=100000G --time=$BT run" > "$d/mem.txt" 2>&1
  rec "$tag" mem_mibs "$(grep -o '([0-9.]* MiB/sec)' "$d/mem.txt" | tr -d '(' | awk '{print $1}')"

  p0=$(psi "$cg/io.pressure"); t0=$(date +%s%N)
  vexec "$v" "fio --name=v --filename=/root/fio.dat --size=1G --rw=randread --bs=4k --iodepth=16 --ioengine=libaio --direct=1 --runtime=$BT --time_based --output-format=json --output=/tmp/f.json >/dev/null 2>&1; cat /tmp/f.json" > "$d/fio.json" 2>&1
  p1=$(psi "$cg/io.pressure"); t1=$(date +%s%N)
  read -r iops p99 <<< "$(python3 - "$d/fio.json" <<'PY'
import json,sys
try:
    t=open(sys.argv[1]).read(); t=t[t.index('{'):]
    j=json.loads(t)['jobs'][0]['read']
    print(f"{j['iops']:.1f} {j['clat_ns']['percentile']['99.000000']/1000:.1f}")
except Exception:
    print("NA NA")
PY
)"
  rec "$tag" io_iops "$iops"; rec "$tag" io_p99_us "$p99"
  rec "$tag" io_psi_pct "$(psi_pct "$p0" "$p1" "$t0" "$t1")"

  vexec "$v" "python3 /root/wake.py $BT" > "$d/wake.txt" 2>&1
  read -r _ w50 w99 w999 wmax _ <<< "$(grep '^WAKE' "$d/wake.txt" || echo 'WAKE NA NA NA NA 0')"
  rec "$tag" wake_p50_us "$w50"; rec "$tag" wake_p99_us "$w99"; rec "$tag" wake_p999_us "$w999"; rec "$tag" wake_max_us "$wmax"

  kill "$spid" 2>/dev/null; wait "$spid" 2>/dev/null
  rec "$tag" overlap_mean "$(awk -F'\t' '{s+=$1;n++}END{if(n)printf "%.2f",s/n}' "$d/place.txt")"
  rec "$tag" overlap_max  "$(awk -F'\t' 'NR==1||$1>m{m=$1}END{print m}' "$d/place.txt")"
  rec "$tag" placement_changes "$(cut -f2,3 "$d/place.txt" | uniq | wc -l | awk '{print ($1>0)?$1-1:0}')"
}
na_count(){ grep "^[0-9]*,$1," "$R/raw.csv" | grep -v '_psi_pct,' | grep -c ',NA$'; }   # PSI is optional telemetry
done_already(){ [ -f "$R/raw.csv" ] && grep -q ",$1,done," "$R/raw.csv"; }
purge_tag(){  # drop partial rows of an interrupted config before re-running it
  local tmp; tmp=$(mktemp)
  grep -v "^[0-9]*,$1," "$R/raw.csv" > "$tmp"; cat "$tmp" > "$R/raw.csv"
  awk -F'\t' -v t="$1" '($1","$2","$3","$4","$5)!=t' "$R/placement.tsv" > "$tmp"; cat "$tmp" > "$R/placement.tsv"
  rm -f "$tmp"; rm -rf "$R/raw/${1//,/_}"
}

cfg_list(){  # pol rep v -> 'none:0' + shuffled stress configs + 'post:0'
  local seed s n
  seed=$(echo "$1$2$3" | cksum | cut -d' ' -f1)
  echo "none:0"
  for s in $STRESSES; do for n in $NLIST; do echo "$s:$n"; done; done \
    | awk -v s="$seed" 'BEGIN{srand(s)}{print rand()"\t"$0}' | sort -k1,1 | cut -f2
  echo "post:0"
}
pol_order(){  # rep -> POLICIES rotated by (rep-1)   (Latin square over reps)
  local -a a=($POLICIES); local k=${#a[@]} i out=""
  for i in $(seq 0 $((k-1))); do out="$out ${a[$(( (i + $1 - 1) % k ))]}"; done
  echo $out
}
vic_order(){  # rep pol -> alternate which victim goes first
  local -a a=($POLICIES); local i idx=0
  for i in "${!a[@]}"; do [ "${a[$i]}" = "$2" ] && idx=$i; done
  if [ $(( ($1 + idx) % 2 )) = 1 ]; then echo "vm ct"; else echo "ct vm"; fi
}

# ---------------- preflight: every policy x victim, short, before the long run ----------------
do_preflight(){
  local saveR=$R saveBT=$BT pol v c s n tag
  R=$W/preflight; rm -rf "$R"; mkdir -p "$R/raw"; BT=3
  echo "ts,policy,rep,victim,stress,n,metric,value" > "$R/raw.csv"
  log "== PREFLIGHT (policies={$POLICIES}, ${BT}s benchmarks)"
  for pol in $POLICIES; do
    apply_policy "$pol"
    for v in vm ct; do
      activate "$v"; enforce_victim "$pol" "$v"
      for c in none:0 cpu:2; do
        s=${c%%:*}; n=${c##*:}; tag="$pol,0,$v,$s,$n"
        [ "$s" = none ] || start_noise "$s" "$n"
        bench "$v" "$tag"
        [ "$s" = none ] || stop_noise
        [ "$(na_count "$tag")" -gt 0 ] && die "PREFLIGHT failed: NA metrics for $tag — see $R/raw/${tag//,/_}"
      done
      log "preflight $pol/$v OK  victim_cpus=$(victim_cpus "$v")  cpu_eps(none,cpu:2)=$(grep ",$pol,0,$v,[a-z]*,[0-9]*,cpu_eps," "$R/raw.csv" | cut -d, -f8 | tr '\n' ' ')"
    done
  done
  R=$saveR; BT=$saveBT
  log "== PREFLIGHT PASSED — main run starts now (safe to close the window)"
}

do_run(){
  log "== RUN  policies={$POLICIES} reps=$REPS bt=${BT}s n={$NLIST} stress={$STRESSES}"
  check_all_ours
  [ -f "$R/raw.csv" ] || echo "ts,policy,rep,victim,stress,n,metric,value" > "$R/raw.csv"
  [ -f "$R/placement.tsv" ] || printf 'policy\trep\tvictim\tstress\tn\tvictim_cpus\toverlap\tnbr_cpus\n' > "$R/placement.tsv"
  local pol rep v c s n tag total=0 cnt=0 need cur="" t_start
  for rep in $(seq "$REPS"); do for pol in $POLICIES; do for v in vm ct; do
    total=$((total + $(cfg_list "$pol" "$rep" "$v" | wc -l))); done; done; done
  t_start=$(date +%s)

  for rep in $(seq "$REPS"); do
    for pol in $(pol_order "$rep"); do
      for v in $(vic_order "$rep" "$pol"); do
        need=0
        for c in $(cfg_list "$pol" "$rep" "$v"); do done_already "$pol,$rep,$v,${c%%:*},${c##*:}" || need=1; done
        if [ $need = 0 ]; then cnt=$((cnt + $(cfg_list "$pol" "$rep" "$v" | wc -l))); continue; fi
        [ "$cur" = "$pol" ] || { apply_policy "$pol"; cur=$pol; }
        log "-- rep=$rep policy=$pol victim-$v: activating + warm-up"
        activate "$v"; enforce_victim "$pol" "$v"
        vexec "$v" "sysbench cpu --threads=$VICT_CORES --time=20 run >/dev/null 2>&1; fio --name=w --filename=/root/fio.dat --size=1G --rw=randread --bs=4k --iodepth=16 --ioengine=libaio --direct=1 --runtime=10 --time_based >/dev/null 2>&1; python3 /root/wake.py 3 >/dev/null"
        for c in $(cfg_list "$pol" "$rep" "$v"); do
          s=${c%%:*}; n=${c##*:}; cnt=$((cnt+1)); tag="$pol,$rep,$v,$s,$n"
          done_already "$tag" && continue
          purge_tag "$tag"
          log "[$cnt/$total] rep=$rep policy=$pol victim=$v stress=$s n=$n"
          case $s in none|post) ;; *) start_noise "$s" "$n" ;; esac
          printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$pol" "$rep" "$v" "$s" "$n" "$(victim_cpus "$v")" "$(overlap "$v" "$n")" "$(nbr_cpus)" >> "$R/placement.tsv"
          bench "$v" "$tag"
          case $s in none|post) sleep 3 ;; *) stop_noise ;; esac
          if [ "$pol" = dedicated ] && grep -qE "^[0-9]*,$tag,(overlap_max,[1-9]|placement_changes,[1-9])" "$R/raw.csv"; then
            log "WARN: dedicated placement violated during $tag (victim shared CPUs or placement moved) — flagged in raw.csv"
          fi
          if [ "$(na_count "$tag")" -gt 0 ]; then
            log "WARN: $(na_count "$tag") metric(s) NA for $tag — see $R/raw/${tag//,/_}"
            [ "$s" = none ] && die "baseline produced NA metrics — stopping early so the run is not wasted"
          fi
          rec "$tag" "done" 1
        done
        log "   elapsed $(( ($(date +%s)-t_start)/60 )) min"
      done
    done
  done
  deactivate_all
  for c in $NBR_IDS; do ct_stop "$c"; done
  do_summary
  log "== ALL DONE — results in $R"
}

# ---------------- summary ----------------
do_summary(){
  python3 - "$R/raw.csv" "$R/summary.csv" <<'PY'
import csv,sys,statistics as st,collections
val={}
for r in csv.DictReader(open(sys.argv[1])):
    if r['metric']=='done': continue
    try: x=float(r['value'])
    except ValueError: continue
    val[(r['policy'],r['rep'],r['victim'],r['stress'],int(r['n']),r['metric'])]=x
absv=collections.defaultdict(list); rel=collections.defaultdict(list)
for (p,rep,v,s,n,m),x in val.items():
    absv[(p,v,s,n,m)].append(x)
    b=val.get((p,rep,v,'none',0,m))            # same-block baseline
    if b: rel[(p,v,s,n,m)].append(x/b)
f=lambda xs,fn: f"{fn(xs):.3f}" if xs else ""
with open(sys.argv[2],'w',newline='') as fo:
    w=csv.writer(fo)
    w.writerow(['policy','victim','stress','n','metric','abs_mean','abs_std','rel_mean','rel_min','rel_max','count'])
    for k in sorted(absv):
        a=absv[k]; r=rel.get(k,[])
        w.writerow([*k,f(a,st.mean),f"{st.stdev(a):.3f}" if len(a)>1 else "",f(r,st.mean),f(r,min),f(r,max),len(a)])
mets=['cpu_eps','mem_mibs','io_iops','io_p99_us','wake_p99_us']
nmax=max([k[3] for k in absv] or [0])
print(f"\nrel. to same-block baseline at n={nmax} (throughput: <1 worse; latency: >1 worse); 'post' = drift check")
print(f"{'policy':10}{'stress':7}"+"".join(f"{m+'/'+v:>16}" for m in mets for v in ('vm','ct')))
for p in sorted({k[0] for k in absv}):
    for s in ('cpu','mem','io','post'):
        n=0 if s=='post' else nmax
        cells=[f"{st.mean(rel[(p,v,s,n,m)]):16.3f}" if rel.get((p,v,s,n,m)) else f"{'-':>16}" for m in mets for v in ('vm','ct')]
        print(f"{p:10}{s:7}"+"".join(cells))
PY
}

# ---------------- clean ----------------
do_clean(){
  local k id
  if ! own "$VMID_VM" victim-vm vm; then qm stop "$VMID_VM" >/dev/null 2>&1; qm destroy "$VMID_VM" --purge && log "destroyed victim-vm"; fi
  if ! own "$VMID_CT" victim-ct ct; then pct stop "$VMID_CT" >/dev/null 2>&1; pct destroy "$VMID_CT" --purge && log "destroyed victim-ct"; fi
  for k in $(seq 8); do id=$(nbr_id "$k")
    if ! own "$id" "nbr$k" ct; then pct stop "$id" >/dev/null 2>&1; pct destroy "$id" --purge && log "destroyed nbr$k"; fi
  done
}

# ---------------- entry ----------------
case "${1:-}" in
  all)
    if pgrep -f "osproj[.]sh _all" >/dev/null; then echo "already running — showing log (Ctrl+C only stops viewing)"
    else nohup setsid bash "$SELF" _all >> "$LOG" 2>&1 < /dev/null & sleep 1
         echo "started in background — Ctrl+C only stops viewing; check later with: bash $SELF status"; fi
    tail -n 30 -f "$LOG" ;;
  _all)
    exec 9>"$W/lock"; flock -n 9 || { log "another run holds the lock"; exit 1; }
    mkdir -p "$R/raw"
    do_build || exit 1
    if [ "$PREFLIGHT" = 1 ] && [ ! -f "$R/raw.csv" ]; then do_preflight || exit 1; fi
    do_run ;;
  status)
    pgrep -f "osproj[.]sh _all" >/dev/null && echo "RUNNING" || echo "NOT RUNNING"
    echo "configs done: $( [ -f "$R/raw.csv" ] && grep -c ',done,' "$R/raw.csv" || echo 0 )"
    tail -n 8 "$LOG" 2>/dev/null ;;
  summary) do_summary ;;
  clean)   do_clean ;;
  *) echo "usage: bash $0 all|status|summary|clean"; exit 1 ;;
esac
