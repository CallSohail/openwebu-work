<#
.SYNOPSIS
  Sets up fastfetch and OPEN_TERMINAL_INFO so an Open WebUI assistant reads this
  Windows PC's system info reliably. Works for any user; nothing is hardcoded.

.DESCRIPTION
  1. Downloads the latest fastfetch (portable, no admin) into the current user's
     tools folder and adds it to the user PATH.
  2. Builds an OPEN_TERMINAL_INFO note (the machine facts + the right commands)
     and sets it as a user environment variable, for Open Terminal started by hand.
  3. Prints the same note to paste into Tether's Open Terminal environment-variable
     box, because Tether strips OS-level OPEN_TERMINAL_* variables from the server.

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File .\setup-system-info.ps1

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File .\setup-system-info.ps1 -WorkFolder "D:\AI"
#>
param(
  [string]$InstallDir = (Join-Path $env:USERPROFILE "tools\fastfetch"),
  [string]$WorkFolder = (Join-Path $env:USERPROFILE "Desktop\Tether")
)

$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

# 1. Pick the right build for this CPU.
$arch = if ($env:PROCESSOR_ARCHITECTURE -eq "ARM64") { "windows-aarch64" } else { "windows-amd64" }
Write-Host "Looking up the latest fastfetch ($arch)..."
$asset = (Invoke-RestMethod "https://api.github.com/repos/fastfetch-cli/fastfetch/releases/latest").assets |
  Where-Object { $_.name -like "*$arch.zip" } | Select-Object -First 1
if (-not $asset) { throw "No $arch build found in the latest fastfetch release." }

New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
$zip = Join-Path $InstallDir "fastfetch.zip"
Write-Host "Downloading $($asset.name)..."
Invoke-WebRequest $asset.browser_download_url -OutFile $zip
Expand-Archive $zip -DestinationPath $InstallDir -Force
Remove-Item $zip

$exe = Get-ChildItem $InstallDir -Recurse -Filter fastfetch.exe | Select-Object -First 1
if (-not $exe) { throw "fastfetch.exe not found after extracting." }
$binDir = $exe.Directory.FullName
Write-Host "Installed: $($exe.FullName)" -ForegroundColor Green

# 2. Add its folder to the user PATH (no admin, idempotent).
$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
if ($userPath -notlike "*$binDir*") {
  [Environment]::SetEnvironmentVariable("Path", "$userPath;$binDir", "User")
  Write-Host "Added to PATH: $binDir" -ForegroundColor Green
} else {
  Write-Host "Already on PATH: $binDir" -ForegroundColor Yellow
}
$env:Path += ";$binDir"

# 3. Build the OPEN_TERMINAL_INFO note, with this machine's real paths.
$ffPath = $exe.FullName
$info = @"
This computer runs Windows and commands execute through cmd.exe (not PowerShell, not Linux). To run PowerShell, use: powershell -NoProfile -Command "<code>", or write a .ps1 file and run it with -File.
For system information (CPU, GPU, RAM, disks, uptime, battery, display) run fastfetch and read its JSON: fastfetch --format json  (full path if PATH is stale: "$ffPath" --format json). fastfetch works on Windows; it is not a Linux-only tool and it is installed here. fastfetch JSON sizes are in bytes (GB = value / 1073741824).
For other facts without fastfetch, prefer: Get-ComputerInfo | ConvertTo-Json  and  Get-CimInstance <class> | ConvertTo-Json. Never parse systeminfo or wmic text.
Default work folder: $WorkFolder. Verify results before reporting. Ask before deleting, moving or overwriting files, or changing system settings.
"@

[Environment]::SetEnvironmentVariable("OPEN_TERMINAL_INFO", $info, "User")
Write-Host "`nSet OPEN_TERMINAL_INFO for this user (used when Open Terminal is started by hand)." -ForegroundColor Green

Write-Host "`nTesting fastfetch..." -ForegroundColor Cyan
& $ffPath --version

Write-Host "`n============================================================" -ForegroundColor Cyan
Write-Host " NEXT STEPS" -ForegroundColor Cyan
Write-Host "============================================================"
Write-Host "A) If you start Open Terminal BY HAND: close it and open a NEW"
Write-Host "   PowerShell, then start it again. It now has fastfetch and"
Write-Host "   OPEN_TERMINAL_INFO."
Write-Host ""
Write-Host "B) If Open Terminal runs through TETHER: Tether ignores the"
Write-Host "   system OPEN_TERMINAL_INFO, so add it in Tether instead:"
Write-Host "     Settings > Open Terminal > (Advanced) environment variables"
Write-Host "     Name:  OPEN_TERMINAL_INFO"
Write-Host "     Value: (the text between the lines below)"
Write-Host "   Then click Restart."
Write-Host "------------------------------------------------------------"
Write-Host $info
Write-Host "------------------------------------------------------------"
