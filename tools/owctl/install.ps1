<#
.SYNOPSIS
  Installs owctl.py into the work folder and prints the command the assistant should call.
.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1
.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1 -Dest "C:\Users\Pret\Desktop\Tether\owctl"
#>
param(
  [string]$Dest = (Join-Path $env:USERPROFILE "Desktop\Tether\owctl")
)
$ErrorActionPreference = "Stop"

# Find a Python launcher.
$py = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $py) { $py = (Get-Command py -ErrorAction SilentlyContinue).Source }
if (-not $py) { Write-Host "Python not found. Install Python 3.8+ first." -ForegroundColor Red; exit 1 }

New-Item -ItemType Directory -Force -Path $Dest | Out-Null
Copy-Item (Join-Path $PSScriptRoot "owctl.py") $Dest -Force
$target = Join-Path $Dest "owctl.py"
Write-Host "Installed: $target" -ForegroundColor Green

Write-Host "`nVerifying..." -ForegroundColor Cyan
& $py $target selftest | Out-Host

Write-Host "`n============================================================" -ForegroundColor Cyan
Write-Host " The assistant should call owctl like this:" -ForegroundColor Cyan
Write-Host "   python `"$target`" sysinfo"
Write-Host "   python `"$target`" scan --path $env:USERPROFILE --top 20"
Write-Host "   python `"$target`" plan --categories temp,downloads-old --days 30"
Write-Host "============================================================"
Write-Host "Install the 'owctl' skill too (skills\windows\owctl\SKILL.md) so the"
Write-Host "model knows when to use it, then restart Open Terminal."
