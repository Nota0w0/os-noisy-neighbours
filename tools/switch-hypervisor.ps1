# 在「Proxmox (VMware 巢狀虛擬化)」與「WSL 2」之間切換 Windows hypervisor。
# 用法：雙擊 switch-hypervisor.bat，或
#   powershell -ExecutionPolicy Bypass -File switch-hypervisor.ps1 [-Mode status|proxmox|wsl]
# 只改開機設定 hypervisorlaunchtype，重開機後生效。不會動「記憶體完整性」等安全性設定。
param(
  [ValidateSet('menu', 'status', 'proxmox', 'wsl')]
  [string]$Mode = 'menu',
  [switch]$Hold      # 內部使用：以管理員身分重新啟動時，結束前停住視窗
)
$ErrorActionPreference = 'Stop'

# ---- 需要系統管理員權限：不是的話重新以管理員身分啟動自己
$me = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $me.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
  Start-Process powershell.exe -Verb RunAs -ArgumentList @(
    '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$PSCommandPath`"", '-Mode', $Mode, '-Hold')
  exit
}

function Get-LaunchType {
  # 「hypervisorlaunchtype」這個鍵名不會被翻譯；沒有這一行代表沒設定過
  $line = & bcdedit.exe /enum '{current}' | Where-Object { $_ -match '^\s*hypervisorlaunchtype\s+(\S+)' }
  if ($line) { ($line -replace '^\s*hypervisorlaunchtype\s+', '').Trim().ToLower() } else { 'unset' }
}

function Show-Status {
  $boot    = Get-LaunchType
  $running = [bool](Get-CimInstance Win32_ComputerSystem).HypervisorPresent
  $hvci    = $false
  try {
    $k = 'HKLM:\SYSTEM\CurrentControlSet\Control\DeviceGuard\Scenarios\HypervisorEnforcedCodeIntegrity'
    $hvci = ((Get-ItemProperty -Path $k -Name Enabled -ErrorAction Stop).Enabled -eq 1)
  } catch { }

  Write-Host ''
  Write-Host '目前狀態' -ForegroundColor Cyan
  if ($running) { Write-Host '  這次開機：hypervisor 執行中  -> 可用 WSL 2，Proxmox 裡沒有 KVM' }
  else          { Write-Host '  這次開機：hypervisor 未執行  -> 可跑 Proxmox（巢狀 VT-x），WSL 2 不能用' }
  switch ($boot) {
    'off'   { Write-Host '  下次開機：關閉 (Proxmox 模式)' }
    'auto'  { Write-Host '  下次開機：開啟 (WSL 2 模式)' }
    default { Write-Host "  下次開機：$boot（未明確設定，是否啟動取決於已安裝的 Windows 功能）" }
  }
  if (($boot -eq 'off' -and $running) -or ($boot -eq 'auto' -and -not $running)) {
    Write-Host '  設定和目前狀態不一致：需要重開機才會生效。' -ForegroundColor Yellow
  }
  if ($hvci) {
    Write-Host '  注意：「記憶體完整性」是開啟的，它會讓 hypervisor 繼續啟動。' -ForegroundColor Yellow
    Write-Host '        要跑 Proxmox 請到 Windows 安全性 > 裝置安全性 > 核心隔離 手動關閉。' -ForegroundColor Yellow
  }
  Write-Host ''
}

function Set-LaunchType([string]$value, [string]$label) {
  & bcdedit.exe /set hypervisorlaunchtype $value | Out-Null
  if ($LASTEXITCODE) { throw "bcdedit 失敗 (exit code $LASTEXITCODE)" }
  if ((Get-LaunchType) -ne $value) { throw "設定沒有寫入：目前是 $(Get-LaunchType)" }
  Write-Host "已設定為 $label，重開機後生效。" -ForegroundColor Green
  if ($value -eq 'off') {
    Write-Host '重開機後在 Proxmox 裡執行  ls /dev/kvm  確認 KVM 可用。'
  } else {
    Write-Host '重開機後執行  wsl --status  確認 WSL 2 可用。'
  }
  $a = Read-Host '現在重開機嗎？請先存檔並關閉 VMware。(y/N)'
  if ($a -match '^[Yy]') { shutdown.exe /r /t 10 /c 'switch-hypervisor: 10 秒後重開機'; Write-Host '10 秒後重開機（要取消請執行 shutdown /a）' }
}

try {
Show-Status
switch ($Mode) {
  'status'  { }
  'proxmox' { Set-LaunchType 'off'  'Proxmox 模式 (hypervisor 關閉)' }
  'wsl'     { Set-LaunchType 'auto' 'WSL 2 模式 (hypervisor 開啟)' }
  'menu' {
    Write-Host '下次開機要用哪一個？'
    Write-Host '  1) Proxmox  (VMware 巢狀虛擬化；WSL 2、Docker Desktop 不能用)'
    Write-Host '  2) WSL 2    (Docker Desktop 也可用；Proxmox 裡的 VM 跑不起來)'
    Write-Host '  3) 不變更，離開'
    switch (Read-Host '請輸入 1 / 2 / 3') {
      '1' { Set-LaunchType 'off'  'Proxmox 模式 (hypervisor 關閉)' }
      '2' { Set-LaunchType 'auto' 'WSL 2 模式 (hypervisor 開啟)' }
      default { Write-Host '沒有變更。' }
    }
  }
}
} catch {
  Write-Host "錯誤：$($_.Exception.Message)" -ForegroundColor Red
} finally {
  if ($Hold) { Read-Host '按 Enter 關閉視窗' | Out-Null }
}
