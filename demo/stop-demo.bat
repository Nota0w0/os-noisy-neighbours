@echo off
rem Double-click: shut the Proxmox VM down cleanly.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-demo.ps1" -Stop
pause
