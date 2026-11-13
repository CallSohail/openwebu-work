---
name: windows-system-info
description: Read hardware, operating system, performance and network information from the user's Windows PC the reliable way, returning structured data instead of guessing at commands. Use whenever the user asks about their CPU, GPU, RAM, disks, storage space, battery, drivers, uptime, running processes, installed programs, network, or any "what does my computer have / how is my PC doing" question.
---

# Windows System Info

Follow `windows-safe-terminal` as well. Everything here is **read-only**: never change settings, stop processes or delete anything from these commands.

Windows has no `/proc` or `lsblk`. The reliable rule on Windows is: **get JSON, then read it.** Never parse the text of `systeminfo` or `wmic`; both are slow, inconsistent, and `wmic` is deprecated. Prefer the commands below, in this order.

## 1. Everything at once: fastfetch (preferred)

Check once whether it is installed: `where.exe fastfetch`. If it is:

```
fastfetch --format json
```

This returns CPU, GPU, memory, disks, OS, host, uptime and display as labelled JSON. Read the fields you need from it. For a few modules only:

```
fastfetch -s CPU:GPU:Memory:Disk:OS --format json
```

If `where.exe fastfetch` finds nothing, tell the user they can install it with `winget install fastfetch` for the best results, then use the built-in commands below for now. Do not install it yourself without asking.

## 2. Built-in fallback (no install)

Always pipe to `ConvertTo-Json`. Run through PowerShell:

```
powershell -NoProfile -Command "Get-ComputerInfo | ConvertTo-Json -Depth 3"
```

That one object covers OS, build, BIOS, CPU and RAM. For specific topics, use CIM and select only the fields you need (full objects are large):

| You want | Command (inside `powershell -NoProfile -Command "..."`) |
| --- | --- |
| CPU | `Get-CimInstance Win32_Processor \| Select-Object Name,NumberOfCores,NumberOfLogicalProcessors,MaxClockSpeed \| ConvertTo-Json` |
| RAM total and free | `Get-CimInstance Win32_OperatingSystem \| Select-Object TotalVisibleMemorySize,FreePhysicalMemory \| ConvertTo-Json` |
| GPU | `Get-CimInstance Win32_VideoController \| Select-Object Name,DriverVersion,AdapterRAM \| ConvertTo-Json` |
| Disks and free space | `Get-CimInstance Win32_LogicalDisk -Filter "DriveType=3" \| Select-Object DeviceID,Size,FreeSpace \| ConvertTo-Json` |
| Uptime | `(Get-Date) - (Get-CimInstance Win32_OperatingSystem).LastBootUpTime` |
| Battery | `Get-CimInstance Win32_Battery \| Select-Object EstimatedChargeRemaining,BatteryStatus \| ConvertTo-Json` |
| Top processes by memory | `Get-Process \| Sort-Object WorkingSet64 -Descending \| Select-Object -First 10 Name,Id,@{n='MB';e={[math]::Round($_.WorkingSet64/1MB)}} \| ConvertTo-Json` |
| Top processes by CPU | `Get-Process \| Sort-Object CPU -Descending \| Select-Object -First 10 Name,Id,CPU \| ConvertTo-Json` |
| Installed programs | `Get-ItemProperty HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*,HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\* \| Where-Object DisplayName \| Select-Object DisplayName,DisplayVersion,Publisher \| Sort-Object DisplayName \| ConvertTo-Json` |
| Network adapters and IPs | `Get-NetIPConfiguration \| ConvertTo-Json -Depth 3` |
| Wi-Fi name and signal | `netsh wlan show interfaces` (text; read the SSID and Signal lines) |
| Windows version | `Get-CimInstance Win32_OperatingSystem \| Select-Object Caption,Version,BuildNumber \| ConvertTo-Json` |

## 3. Turn raw numbers into readable ones

CIM memory and disk sizes are in **kilobytes or bytes**. Convert before showing the user:
- RAM from `Win32_OperatingSystem`: values are in **KB**, so GB = value / 1048576.
- Disk `Size`/`FreeSpace` from `Win32_LogicalDisk`: values are in **bytes**, so GB = value / 1073741824.
- Report used % as `(Size - FreeSpace) / Size * 100`, rounded.

## 4. Presenting

- Show the result as a small table or a few labelled lines, not raw JSON, unless the user asks for raw.
- Round sizes to GB with one decimal and show free space as both GB and a percentage.
- If a command returns nothing (for example no battery on a desktop), say so plainly rather than guessing.
- For a full summary, you may write a `system-report.md` in the work folder with OS, CPU, GPU, RAM, disks and network as tables, then display it.
