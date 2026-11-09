---
name: system-diagnostics
description: Inspect and troubleshoot the user's own computer without changing it - OS and hardware, disk space, slow PC, Wi-Fi and DNS, proxy, printers, startup items, event logs, installed tools, Python and PATH problems, Windows Update history. Use for "my PC is slow", "why does pip fail", "what is installed" and IT-support style checks. Read-only unless the user approves a fix.
---

# System diagnostics

## Start with a snapshot

`python -B scripts/sysinfo.py` prints OS, user, disks with free space, proxy variables, whether Tether's sandbox is on, installed tools (python, uv, git, node, docker, ffmpeg, pandoc, LibreOffice, tesseract, coding agents) and PATH entries that point nowhere. Add `--json` to save it.

## Windows read-only commands (PowerShell)

| Question | Command |
| --- | --- |
| Top memory users | `Get-Process \| Sort-Object WS -Descending \| Select-Object -First 15 Name,Id,@{n='MB';e={[int]($_.WS/1MB)}}` |
| Startup items | `Get-CimInstance Win32_StartupCommand \| Select Name,Command,Location,User` |
| Recent errors | `Get-WinEvent -FilterHashtable @{LogName='System';Level=1,2;StartTime=(Get-Date).AddDays(-7)} \| Group-Object ProviderName \| Sort Count -Desc` |
| Network | `Get-NetIPConfiguration`, `Resolve-DnsName <host>`, `Test-NetConnection <host> -Port 443` |
| Proxy | `netsh winhttp show proxy`, plus `HTTP(S)_PROXY` env vars |
| Installed apps | `winget list` or `Get-ItemProperty HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\* \| Select DisplayName,DisplayVersion,InstallDate` |
| Updates | `Get-HotFix \| Sort InstalledOn -Desc \| Select -First 30` |
| Battery | `powercfg /batteryreport /output .scratch\<task>\battery.html` |
| Disk health | `Get-PhysicalDisk \| Select FriendlyName,HealthStatus,OperationalStatus` |
| Pending reboot | test `HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired` |

macOS and Linux equivalents: `top -l 1`/`top -bn1`, `df -h`, `scutil --proxy`, `journalctl -p err -S -7d`, `system_profiler SPHardwareDataType`.

## Common causes worth checking first

- pip or uv fails to build a package: Python is too new for the package's wheels (Python 3.15 and PyYAML on Windows, for example). Use `uv run --python 3.12 ...` rather than installing compilers.
- Requests time out on a campus network: a proxy is required for outbound HTTP; check env vars and WinHTTP.
- "python" opens the Microsoft Store: the App Execution Alias is on; use `py` or the full path.
- Disk full: run file-finder `sizes` and `large` on the home folder before blaming Windows.

## Fixes

Write the fix as a numbered plan with the exact commands and what each changes, then wait for approval. Prefer user-level changes over admin ones. Never disable antivirus, firewall, UAC or updates. Save reports in the scratch folder; keep an HTML report only if the user wants it.
