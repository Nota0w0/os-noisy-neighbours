#!/usr/bin/env bash
# One-time setup on the Proxmox host (as root) so that the live demo starts by itself at boot.
#
#   bash demo/install-autostart.sh            # install + start now
#   bash demo/install-autostart.sh --remove   # turn auto-start off again (do this after the talk)
#
# After this, demo/start-demo.bat on the Windows side only has to power on the VMware VM and open
# the browser: no console login, no typing in the Proxmox shell.
# While the service is enabled, every boot starts all project guests and keeps both victims busy,
# so remove it before running measurements with osproj.sh.
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "run as root"; exit 1; }
REPO=$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)
UNIT=/etc/systemd/system/os-demo.service

if [ "${1:-}" = "--remove" ]; then
  systemctl disable --now os-demo.service 2>/dev/null || true
  rm -f "$UNIT"; systemctl daemon-reload
  echo "auto-start removed (guests keep running; stop them in the Proxmox UI if needed)"
  exit 0
fi

# open-vm-tools lets VMware read this host's IP address (start-demo.bat uses it) and shut it down cleanly
if ! dpkg -s open-vm-tools >/dev/null 2>&1; then
  echo "installing open-vm-tools …"
  apt-get install -y -qq open-vm-tools >/dev/null || echo "WARNING: could not install open-vm-tools (start-demo.bat then needs the IP in demo\\pve-ip.txt)"
fi

cat > "$UNIT" <<EOF
[Unit]
Description=OS term project live demo (demo/demo.py)
Wants=network-online.target
After=network-online.target pve-cluster.service pvedaemon.service pve-guests.service
StartLimitIntervalSec=300
StartLimitBurst=5

[Service]
Type=simple
WorkingDirectory=$REPO
ExecStartPre=-/usr/bin/git -C $REPO pull --ff-only -q
ExecStart=/usr/bin/python3 -u $REPO/demo/demo.py --port 8080
Restart=on-failure
RestartSec=15
KillSignal=SIGTERM
TimeoutStopSec=90

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now os-demo.service
IP=$(ip -4 -br addr show vmbr0 | awk '{print $3}' | cut -d/ -f1)
echo
echo "auto-start installed. The demo starts on every boot (logs: journalctl -u os-demo -f)."
echo "demo page:       http://$IP:8080/"
echo "Proxmox console: https://$IP:8006/"
echo "Turn it off after the talk: bash $REPO/demo/install-autostart.sh --remove"
