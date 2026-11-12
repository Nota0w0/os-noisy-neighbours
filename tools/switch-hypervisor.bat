@echo off
rem Double-click: choose Proxmox (VMware nested) or WSL 2 for the next boot.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0switch-hypervisor.ps1" %*
pause
