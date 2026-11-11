#!/usr/bin/env bash
# =====================================================================
#  provision.sh — INF-02 / INF-03 / INF-04
#  Run on the Proxmox VE host as root, after host/preflight.sh.
#
#    provision.sh all        VM 201 + CT 301 + neighbour template 400 + clones 401-408
#    provision.sh vm|ct|nbr  one part only
#    provision.sh push       re-copy guest/bench_*.sh into both victims
#    provision.sh destroy --yes   stop and delete every guest this project created
#
#  Idempotent: an ID that already exists is left alone. Settings follow
#  the Proxmox GUI defaults except where config/experiment.toml says
#  otherwise (cache=none, fixed VM IP, no auto-start).
# =====================================================================
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CFG() { python3 "$REPO/host/cfg.py" "$1"; }
log() { printf '\033[1;32m[provision]\033[0m %s\n' "$*"; }
die() { printf '\033[1;31m[provision] ERROR:\033[0m %s\n' "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "run as root on the Proxmox host"
command -v pveversion >/dev/null || die "this is not a Proxmox VE host"
[[ -f "$REPO/docs/platform.json" ]] || die "run host/preflight.sh first"

STORAGE="$(CFG host.storage)";      TSTORE="$(CFG host.template_storage)"
BRIDGE="$(CFG host.bridge)"
VM_IP="$(CFG host.vm_ip)";          CIDR="$(CFG host.cidr)"
GW="$(CFG host.gateway)";           NS="$(CFG host.nameserver)"
VMID="$(CFG ids.vm)";               CTID="$(CFG ids.ct)"
TPL="$(CFG ids.nbr_template)";      NFIRST="$(CFG ids.nbr_first)";  NCOUNT="$(CFG ids.nbr_count)"
VC="$(CFG victim.cores)";           VMEM="$(CFG victim.memory_mb)";  VDISK="$(CFG victim.disk_gb)"
NC="$(CFG neighbour.cores)";        NMEM="$(CFG neighbour.memory_mb)"; NDISK="$(CFG neighbour.disk_gb)"
NIO="$(CFG neighbour.io_file_gb)";  VIO="$(CFG bench.io_file_gb)"

KEY=/root/.ssh/id_ed25519
SSH_OPTS=(-o BatchMode=yes -o ConnectTimeout=5 -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -i "$KEY")
CLOUD_IMG_URL="https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img"
CACHE="$REPO/cache"; mkdir -p "$CACHE"

# Background services that would add noise inside the guests
QUIET_UNITS="unattended-upgrades.service apt-daily.timer apt-daily-upgrade.timer man-db.timer snapd.service snapd.socket"

exists_vm() { qm status "$1" >/dev/null 2>&1; }
exists_ct() { pct status "$1" >/dev/null 2>&1; }
# Never touch a guest this project did not create: an existing ID must carry our name.
owned_vm() { qm config "$1" 2>/dev/null | grep -qx "name: $2"; }
owned_ct() { pct config "$1" 2>/dev/null | grep -qx "hostname: $2"; }
check_vm_id() {   # $1 = id, $2 = expected name
  if exists_vm "$1" && ! owned_vm "$1" "$2"; then
    die "VM ID $1 is already used by another guest ($(qm config "$1" | awk -F': ' '/^name:/{print $2}')). Change [ids] in config/experiment.toml."
  fi
  if exists_ct "$1"; then die "ID $1 is already used by a container. Change [ids] in config/experiment.toml."; fi
}
check_ct_id() {   # $1 = id, $2 = expected hostname
  if exists_ct "$1" && ! owned_ct "$1" "$2"; then
    die "CT ID $1 is already used by another container ($(pct config "$1" | awk -F': ' '/^hostname:/{print $2}')). Change [ids] in config/experiment.toml."
  fi
  if exists_vm "$1"; then die "ID $1 is already used by a VM. Change [ids] in config/experiment.toml."; fi
}
VERDICT="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["verdict"])' "$REPO/docs/platform.json")"
ct_running() { pct status "$1" 2>/dev/null | grep -q running; }
# shellcheck disable=SC2029  # arguments are meant to expand locally before being sent
vm_ssh() { ssh "${SSH_OPTS[@]}" "ubuntu@$VM_IP" "$@"; }

ensure_key() {
  [[ -f $KEY ]] || { log "creating SSH key $KEY"; ssh-keygen -q -t ed25519 -N '' -f "$KEY"; }
}

find_ct_template() {
  local t
  t="$(pveam list "$TSTORE" 2>/dev/null | grep -oE 'ubuntu-24\.04-standard_[^[:space:]]+\.tar\.(zst|gz|xz)' | sort -V | tail -n1 || true)"
  if [[ -z $t ]]; then
    pveam update >/dev/null 2>&1 || true
    t="$(pveam available --section system | awk '{print $2}' | grep -E '^ubuntu-24\.04-standard_' | sort -V | tail -n1 || true)"
    [[ -n $t ]] || die "no ubuntu-24.04-standard template available from pveam"
    log "downloading CT template $t"
    pveam download "$TSTORE" "$t" >&2
  fi
  echo "$t"
}

wait_ct_network() {   # $1 = ctid
  # shellcheck disable=SC2016  # the loop must expand inside the container
  pct exec "$1" -- bash -c 'for _ in $(seq 60); do getent hosts archive.ubuntu.com >/dev/null && exit 0; sleep 2; done; exit 1' \
    || die "CT $1 has no network/DNS after 120 s"
}

ct_install() {        # $1 = ctid, rest = packages
  local id=$1; shift
  wait_ct_network "$id"
  pct exec "$id" -- bash -c "export DEBIAN_FRONTEND=noninteractive; apt-get update -q && apt-get install -y -q $*" \
    || die "apt-get install failed in CT $id"
  pct exec "$id" -- bash -c "systemctl disable --now $QUIET_UNITS >/dev/null 2>&1 || true"
}

push_to_ct() {
  pct exec "$CTID" -- mkdir -p /opt/bench
  for f in bench_cpu.sh bench_io.sh; do
    pct push "$CTID" "$REPO/guest/$f" "/opt/bench/$f" --perms 0755
  done
}

push_to_vm() {
  scp "${SSH_OPTS[@]}" "$REPO/guest/bench_cpu.sh" "$REPO/guest/bench_io.sh" "ubuntu@$VM_IP:/tmp/" >/dev/null
  vm_ssh 'sudo install -d /opt/bench && sudo install -m 0755 /tmp/bench_cpu.sh /tmp/bench_io.sh /opt/bench/'
}

# ---------------------------------------------------------------- VM
provision_vm() {
  if [[ $VERDICT == BLOCKED* ]]; then
    die "platform verdict $VERDICT: KVM is not available, the victim VM cannot run here (README §Platform). 'provision.sh ct' and 'provision.sh nbr' still work."
  fi
  check_vm_id "$VMID" victim-vm
  ensure_key
  if exists_vm "$VMID"; then
    log "VM $VMID exists — skipping creation"
  else
    local img="$CACHE/noble-server-cloudimg-amd64.img"
    if [[ ! -f $img ]]; then
      log "downloading Ubuntu 24.04 cloud image"
      wget -q --show-progress -O "$img.part" "$CLOUD_IMG_URL" && mv "$img.part" "$img"
    fi
    log "creating VM $VMID (${VC} vCPU, ${VMEM} MB, ${VDISK} GB on $STORAGE, cache=none)"
    qm create "$VMID" --name victim-vm --ostype l26 --cpu x86-64-v2-AES --sockets 1 --cores "$VC" \
      --memory "$VMEM" --scsihw virtio-scsi-single --net0 "virtio,bridge=$BRIDGE" \
      --serial0 socket --vga serial0 --onboot 0
    qm set "$VMID" --scsi0 "$STORAGE:0,import-from=$img,cache=none,iothread=1"
    qm resize "$VMID" scsi0 "${VDISK}G"
    qm set "$VMID" --ide2 "$STORAGE:cloudinit" --boot order=scsi0
    qm set "$VMID" --ciuser ubuntu --sshkeys "$KEY.pub" \
      --ipconfig0 "ip=$VM_IP/$CIDR,gw=$GW" --nameserver "$NS"
  fi
  [[ "$(qm status "$VMID")" == *running* ]] || qm start "$VMID" \
    || die "VM $VMID failed to start (no KVM? check docs/host-spec.md verdict)"

  log "waiting for SSH on $VM_IP (first boot runs cloud-init, up to 3 min)"
  local ok=0
  for _ in $(seq 90); do vm_ssh true 2>/dev/null && { ok=1; break; }; sleep 2; done
  ((ok)) || die "cannot SSH to ubuntu@$VM_IP — check host.vm_ip / gateway in config"
  vm_ssh 'cloud-init status --wait >/dev/null 2>&1 || true'

  log "installing sysbench + fio in VM"
  vm_ssh "sudo DEBIAN_FRONTEND=noninteractive bash -c 'apt-get update -q && apt-get install -y -q sysbench fio python3'" \
    || die "apt-get install failed in VM"
  vm_ssh "sudo systemctl disable --now $QUIET_UNITS >/dev/null 2>&1 || true"
  push_to_vm
  log "laying out the ${VIO} GB victim fio file in VM"
  vm_ssh "sudo /opt/bench/bench_io.sh prep $VIO" >/dev/null
  qm config "$VMID" > "$REPO/docs/vm-$VMID.conf"
  log "VM $VMID ready (config saved to docs/vm-$VMID.conf)"
}

# ---------------------------------------------------------------- CT
provision_ct() {
  check_ct_id "$CTID" victim-ct
  if exists_ct "$CTID"; then
    log "CT $CTID exists — skipping creation"
  else
    local t; t="$(find_ct_template)"
    log "creating CT $CTID (${VC} cores, ${VMEM} MB, ${VDISK} GB on $STORAGE)"
    pct create "$CTID" "$TSTORE:vztmpl/$t" --hostname victim-ct --ostype ubuntu \
      --cores "$VC" --memory "$VMEM" --swap 512 --rootfs "$STORAGE:$VDISK" \
      --net0 "name=eth0,bridge=$BRIDGE,ip=dhcp" --unprivileged 1 --features nesting=1 --onboot 0
  fi
  ct_running "$CTID" || pct start "$CTID"
  log "installing sysbench + fio in CT $CTID"
  ct_install "$CTID" sysbench fio python3
  push_to_ct
  log "laying out the ${VIO} GB victim fio file in CT"
  pct exec "$CTID" -- /opt/bench/bench_io.sh prep "$VIO" >/dev/null
  pct config "$CTID" > "$REPO/docs/ct-$CTID.conf"
  log "CT $CTID ready (config saved to docs/ct-$CTID.conf)"
}

# ---------------------------------------------------------- neighbours
provision_nbr() {
  local i id
  check_ct_id "$TPL" nbr-template
  for ((i = 1; i <= NCOUNT; i++)); do check_ct_id "$((NFIRST + i - 1))" "nbr$i"; done
  if exists_ct "$TPL"; then
    log "neighbour template $TPL exists — skipping creation"
  else
    local t; t="$(find_ct_template)"
    log "creating neighbour template CT $TPL (${NC} cores, ${NMEM} MB)"
    pct create "$TPL" "$TSTORE:vztmpl/$t" --hostname nbr-template --ostype ubuntu \
      --cores "$NC" --memory "$NMEM" --swap 512 --rootfs "$STORAGE:$NDISK" \
      --net0 "name=eth0,bridge=$BRIDGE,ip=dhcp" --unprivileged 1 --features nesting=1 --onboot 0
    pct start "$TPL"
    ct_install "$TPL" stress-ng fio
    pct stop "$TPL"
    pct template "$TPL"
  fi

  for ((i = 1; i <= NCOUNT; i++)); do
    id=$((NFIRST + i - 1))
    if exists_ct "$id"; then
      log "neighbour $id exists"
    else
      log "cloning neighbour $id (nbr$i)"
      pct clone "$TPL" "$id" --hostname "nbr$i" 2>/dev/null \
        || pct clone "$TPL" "$id" --hostname "nbr$i" --full 1   # storage without linked clones
    fi
    pct set "$id" --cores "$NC" --memory "$NMEM" --onboot 0     # keep DEC-04 in sync with config
    ct_running "$id" || pct start "$id"
    if ! pct exec "$id" -- test -f /root/nbr.dat; then
      log "  laying out ${NIO} GB fio file in $id"
      pct exec "$id" -- fio --name=prep --filename=/root/nbr.dat --size="$((NIO * 1024))M" \
        --rw=write --bs=1M --direct=1 --ioengine=libaio --iodepth=8 --output=/dev/null
    fi
  done
  log "neighbours $NFIRST..$((NFIRST + NCOUNT - 1)) running and idle (${NC} cores each)"
}

destroy_ct() {   # $1 = id, $2 = expected hostname
  exists_ct "$1" || return 0
  if ! owned_ct "$1" "$2"; then log "CT $1 is not ours (hostname is not $2) — left alone"; return 0; fi
  pct stop "$1" >/dev/null 2>&1 || true
  pct destroy "$1" --purge && log "destroyed CT $1"
}

destroy_all() {
  [[ "${1:-}" == "--yes" ]] || die "destroy deletes VM $VMID, CT $CTID, $TPL and all neighbours; re-run with --yes"
  local i
  for ((i = 1; i <= NCOUNT; i++)); do destroy_ct "$((NFIRST + i - 1))" "nbr$i"; done
  destroy_ct "$TPL" nbr-template
  destroy_ct "$CTID" victim-ct
  if exists_vm "$VMID"; then
    if owned_vm "$VMID" victim-vm; then
      qm stop "$VMID" >/dev/null 2>&1 || true
      qm destroy "$VMID" --purge && log "destroyed VM $VMID"
    else
      log "VM $VMID is not ours (name is not victim-vm) — left alone"
    fi
  fi
  return 0
}

case "${1:-}" in
  all)     if [[ $VERDICT == BLOCKED* ]]; then
             log "platform verdict $VERDICT: skipping the victim VM (no KVM); building CT + neighbours only"
           else
             provision_vm
           fi
           provision_ct; provision_nbr ;;
  vm)      provision_vm ;;
  ct)      provision_ct ;;
  nbr)     provision_nbr ;;
  push)    if exists_vm "$VMID"; then push_to_vm; fi; push_to_ct; log "guest scripts updated" ;;
  destroy) destroy_all "${2:-}" ;;
  *) echo "usage: provision.sh all|vm|ct|nbr|push|destroy --yes" >&2; exit 2 ;;
esac
