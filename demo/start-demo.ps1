# Windows side of the live demo: power on the Proxmox VM in VMware, wait for the demo page, open it.
# Run it by double-clicking start-demo.bat (same folder). Stop with stop-demo.bat.
# Requires demo/install-autostart.sh to have been run once on the Proxmox host.
param(
  [string]$Vmx   = "$env:USERPROFILE\Documents\Virtual Machines\Ubuntun\Ubuntun.vmx",
  [string]$Vmrun = "${env:ProgramFiles(x86)}\VMware\VMware Workstation\vmrun.exe",
  [int]$Port     = 8080,
  [switch]$Console,          # also open the Proxmox web console (https://<ip>:8006)
  [switch]$Stop              # shut the Proxmox VM down cleanly instead
)
$ErrorActionPreference = 'Stop'
$ipFile = Join-Path $PSScriptRoot 'pve-ip.txt'
function Say($m) { Write-Host ("[{0}] {1}" -f (Get-Date -Format 'HH:mm:ss'), $m) }
if (-not (Test-Path $Vmrun)) { throw "vmrun.exe not found at $Vmrun (is VMware Workstation installed?)" }
if (-not (Test-Path $Vmx))   { throw "VM not found: $Vmx  (edit the path at the top of start-demo.ps1)" }

$running = (& $Vmrun -T ws list) -match [regex]::Escape($Vmx)
if ($Stop) {
  if (-not $running) { Say 'VM is not running.'; exit 0 }
  Say 'Shutting down Proxmox (the demo stops its neighbour load first)…'
  & $Vmrun -T ws stop $Vmx soft
  if ($LASTEXITCODE) { Say 'Soft shutdown failed (open-vm-tools missing?). Use Shutdown in the Proxmox UI.'; exit 1 }
  Say 'Done.'; exit 0
}

if ($running) { Say 'Proxmox VM is already running.' }
else {
  Say 'Powering on the Proxmox VM (no VMware window)…'
  & $Vmrun -T ws start $Vmx nogui
  if ($LASTEXITCODE) { throw "vmrun start failed. Close the VM's tab in VMware and try again." }
}

# ---- find the Proxmox IP: ask VMware Tools first, otherwise use the cached one
$ip = $null
Say 'Waiting for Proxmox to boot and report its IP address (up to 3 min)…'
$job = Start-Job { param($r, $v) & $r -T ws getGuestIPAddress $v -wait } -ArgumentList $Vmrun, $Vmx
if (Wait-Job $job -Timeout 180) {
  $out = (Receive-Job $job | Out-String).Trim()
  if ($out -match '^\d{1,3}(\.\d{1,3}){3}$') { $ip = $out }
}
Remove-Job $job -Force
if ($ip) { Set-Content -Path $ipFile -Value $ip -Encoding ascii }
elseif (Test-Path $ipFile) { $ip = (Get-Content $ipFile -TotalCount 1).Trim(); Say "VMware Tools did not answer; using $ip from pve-ip.txt" }
else { throw 'Could not learn the Proxmox IP. Put it (e.g. 192.168.19.128) on one line in demo\pve-ip.txt and run again.' }
Say "Proxmox is at $ip"

# ---- wait for the demo page (the service starts all guests first: 1-3 min)
$url = "http://${ip}:$Port/"
Say "Waiting for the demo page at $url (guests are starting, up to 6 min)…"
$deadline = (Get-Date).AddMinutes(6); $up = $false
while ((Get-Date) -lt $deadline) {
  try { Invoke-WebRequest -UseBasicParsing -TimeoutSec 3 "${url}api/state?since=999999999" | Out-Null; $up = $true; break }
  catch { Start-Sleep -Seconds 3 }
}
if (-not $up) { throw "Demo page did not come up. In the Proxmox console run: journalctl -u os-demo -n 40" }
Start-Process $url
if ($Console) { Start-Process "https://${ip}:8006/" }
Say "Opened $url  — the page shows 'Starting guests…' until both victims report; then press 1, 2, 3 …"
Say "Proxmox console (if needed): https://${ip}:8006/"
