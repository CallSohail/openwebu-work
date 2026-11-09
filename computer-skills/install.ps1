<#
Install the computer-skills pack for Open Terminal / Tether on Windows.

  .\install.ps1                 copy skills to %USERPROFILE%\.agents\skills
  .\install.ps1 -WithAgentsMd   also copy AGENTS.md to %USERPROFILE% (skipped if one exists)
  .\install.ps1 -Only workspace-hygiene,safe-computer-use,profile-lecturer
  .\install.ps1 -Uninstall      remove only the skills this pack installed
  .\install.ps1 -HomeDir D:\TetherHome   install into the folder Open Terminal uses as home
                                (ask the chat: python -c "import pathlib;print(pathlib.Path.home())")

Existing skills with the same name are backed up to .agents\skills-backup-<date> first.
Nothing outside %USERPROFILE%\.agents (and AGENTS.md if asked) is touched.
#>
param(
  [string]$HomeDir = $env:USERPROFILE,
  [switch]$WithAgentsMd,
  [string[]]$Only,
  [switch]$Uninstall
)
$ErrorActionPreference = 'Stop'
$src = Join-Path $PSScriptRoot 'skills'
if (-not (Test-Path $HomeDir -PathType Container)) { throw "Home folder not found: $HomeDir" }
$HomeDir = (Resolve-Path $HomeDir).Path
$dest = Join-Path $HomeDir '.agents\skills'
$skills = Get-ChildItem $src -Directory | Where-Object { -not $Only -or $Only -contains $_.Name }
if (-not $skills) { throw "No matching skills in $src" }

if ($Uninstall) {
  foreach ($s in $skills) {
    $target = Join-Path $dest $s.Name
    if (Test-Path $target) { Remove-Item $target -Recurse -Force; Write-Host "removed  $($s.Name)" }
  }
  return
}

New-Item -ItemType Directory -Force -Path $dest | Out-Null
$backup = Join-Path $HomeDir (".agents\skills-backup-" + (Get-Date -Format 'yyyyMMdd-HHmmss'))
foreach ($s in $skills) {
  $target = Join-Path $dest $s.Name
  if (Test-Path $target) {
    New-Item -ItemType Directory -Force -Path $backup | Out-Null
    Move-Item $target (Join-Path $backup $s.Name)
    Write-Host "backup   $($s.Name) -> $backup"
  }
  Copy-Item $s.FullName $target -Recurse
  Get-ChildItem $target -Recurse -Directory -Filter '__pycache__' | Remove-Item -Recurse -Force
  Write-Host "installed $($s.Name)"
}

if ($WithAgentsMd) {
  $agents = Join-Path $HomeDir 'AGENTS.md'
  if (Test-Path $agents) { Write-Host "AGENTS.md already exists, left unchanged: $agents" }
  else { Copy-Item (Join-Path $PSScriptRoot 'AGENTS.md') $agents; Write-Host "installed AGENTS.md -> $agents (edit the [bracketed] lines)" }
}
Write-Host "`nInstalled into $dest"
Write-Host "Done. In Open WebUI, open a chat with this terminal selected and type `$ to see the skills."
