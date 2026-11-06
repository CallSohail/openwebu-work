<#
.SYNOPSIS
  Copies the Windows Open Terminal skills into the folders Open Terminal reads skills from.

.DESCRIPTION
  Open Terminal lists skills from <home>\.agents\skills\<name>\SKILL.md, where <home> is the
  folder the Open Terminal server was started in:
    - Started by Tether: your user folder (C:\Users\<you>) unless you changed
      "Home directory" in Tether > Settings > Open Terminal.
    - Started by hand with --cwd: that folder.
  By default this script installs into both, so the skills work either way.

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1 -Target "D:\Work\.agents\skills"
#>
param(
  [string[]]$Target = @(
    (Join-Path $env:USERPROFILE ".agents\skills"),
    (Join-Path $env:USERPROFILE "Desktop\Tether\.agents\skills")
  )
)

$ErrorActionPreference = "Stop"
$source = $PSScriptRoot
$skills = Get-ChildItem -Path $source -Directory | Where-Object { Test-Path (Join-Path $_.FullName "SKILL.md") }

if (-not $skills) { throw "No skill folders with a SKILL.md were found next to install.ps1." }

foreach ($root in $Target) {
  New-Item -ItemType Directory -Force -Path $root | Out-Null
  foreach ($skill in $skills) {
    $dest = Join-Path $root $skill.Name
    if (Test-Path $dest) {
      $backup = "$dest.bak-" + (Get-Date -Format "yyyyMMdd-HHmmss")
      Move-Item -Path $dest -Destination $backup
      Write-Host "  kept old copy: $backup"
    }
    Copy-Item -Path $skill.FullName -Destination $dest -Recurse
    Write-Host "Installed $($skill.Name) -> $dest"
  }
}

Write-Host ""
Write-Host "Done. Restart Open Terminal (Tether: Settings > Open Terminal > Restart) so EveGPT sees the skills."
