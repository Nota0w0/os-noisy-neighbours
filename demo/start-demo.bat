@echo off
rem Double-click: power on the Proxmox VM, wait for the live demo, open it in the browser.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-demo.ps1" %*
pause
