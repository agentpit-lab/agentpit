<#
.SYNOPSIS
    Disable or restore sleep-on-AC and Windows Update active hours for an unattended night queue run.

.DESCRIPTION
    -Enable turns off sleep on AC power (powercfg /change standby-timeout-ac 0) and sets Windows
    Update active hours to span the night, so a reboot doesn't land mid-queue. -Disable restores
    the values saved by the previous -Enable. Values are saved to and read from
    night-power-state.json in paceline's state directory ($env:PACELINE_STATE_DIR, else
    $env:CLAUDE_CONFIG_DIR\state\paceline, else ~\.claude\state\paceline), since the plugin
    directory is replaced on every update. Windows only.

    Running with neither switch (or -WhatIf) is a dry run: it prints what -Enable or -Disable
    would change without changing anything. This script is not meant to be invoked with -Enable or
    -Disable directly from an agent session — only the dry run is safe to run unattended; the
    owner runs the real switches themselves, or wires them into a scheduled task.

.PARAMETER Enable
    Disable AC sleep and set active hours to cover the night. Needs elevation for
    Set-WUActiveHoursStart/End (Windows Update) and for the powercfg /setactive step; the
    per-scheme /change calls do not.

.PARAMETER Disable
    Restore the values saved by the last -Enable. Same elevation needs as -Enable.

.PARAMETER WhatIf
    Force the dry run even when -Enable or -Disable is also passed. Default when neither switch
    is given.

.PARAMETER NightStart
    Active-hours start, 0-23. Default 0 (00:00).

.PARAMETER NightEnd
    Active-hours end, 0-23. Default 8 (08:00). Windows Update active hours cap the span at 18
    hours; this script does not widen that cap.
#>
[CmdletBinding()]
param(
    [switch]$Enable,
    [switch]$Disable,
    [switch]$WhatIf,
    [ValidateRange(0, 23)][int]$NightStart = 0,
    [ValidateRange(0, 23)][int]$NightEnd = 8
)

$ErrorActionPreference = 'Stop'
$configDir = if ($env:CLAUDE_CONFIG_DIR) { $env:CLAUDE_CONFIG_DIR } else { Join-Path $HOME '.claude' }
$stateDir = if ($env:PACELINE_STATE_DIR) { $env:PACELINE_STATE_DIR } else { Join-Path (Join-Path $configDir 'state') 'paceline' }
$stateFile = Join-Path $stateDir 'night-power-state.json'

function Get-CurrentAcTimeout {
    # AC standby timeout of the active power scheme, in minutes. Needs no elevation.
    $line = powercfg /query SCHEME_CURRENT SUB_SLEEP STANDBYIDLE | Select-String 'Current AC Power Setting Index'
    if (-not $line) { return $null }
    $hex = ($line -split ':')[1].Trim()
    return [Convert]::ToInt32($hex, 16) / 60
}

function Get-CurrentActiveHours {
    # Reads today's effective active hours from the registry. Needs no elevation to read.
    $key = 'HKLM:\SOFTWARE\Microsoft\WindowsUpdate\UX\Settings'
    $start = (Get-ItemProperty -Path $key -Name 'ActiveHoursStart' -ErrorAction SilentlyContinue).ActiveHoursStart
    $end = (Get-ItemProperty -Path $key -Name 'ActiveHoursEnd' -ErrorAction SilentlyContinue).ActiveHoursEnd
    [pscustomobject]@{ Start = $start; End = $end }
}

$dryRun = $WhatIf -or (-not $Enable -and -not $Disable)

if ($dryRun) {
    $currentTimeout = Get-CurrentAcTimeout
    $currentHours = Get-CurrentActiveHours
    Write-Host "Dry run — nothing changed." -ForegroundColor Yellow
    Write-Host "Current AC standby timeout: $currentTimeout min (0 = never)"
    Write-Host "Current Windows Update active hours: $($currentHours.Start):00-$($currentHours.End):00"
    Write-Host ""
    Write-Host "-Enable would (needs elevation for the active-hours calls):"
    Write-Host "  1. Save the values above to $stateFile"
    Write-Host "  2. powercfg /change standby-timeout-ac 0   (no elevation needed)"
    Write-Host "  3. Set the ActiveHoursStart/ActiveHoursEnd registry values to $NightStart / $NightEnd   (needs elevation; HKLM write)"
    Write-Host ""
    Write-Host "-Disable would (needs elevation, same calls, restoring saved values):"
    if (Test-Path $stateFile) {
        $saved = Get-Content $stateFile -Raw -Encoding utf8 | ConvertFrom-Json
        Write-Host "  Restore AC standby timeout to $($saved.AcTimeoutMin) min"
        Write-Host "  Restore active hours to $($saved.ActiveHoursStart):00-$($saved.ActiveHoursEnd):00"
    } else {
        Write-Host "  No saved state yet at $stateFile — run -Enable first."
    }
    exit 0
}

if ($Enable -and $Disable) {
    Write-Error "Pass only one of -Enable / -Disable."
    exit 1
}

$isElevated = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isElevated) {
    Write-Error "Active-hours changes need an elevated PowerShell session. Re-run as Administrator."
    exit 1
}

if ($Enable) {
    $currentTimeout = Get-CurrentAcTimeout
    $currentHours = Get-CurrentActiveHours
    New-Item -ItemType Directory -Force -Path $stateDir | Out-Null
    [pscustomobject]@{
        AcTimeoutMin     = $currentTimeout
        ActiveHoursStart = $currentHours.Start
        ActiveHoursEnd   = $currentHours.End
        SavedAt          = (Get-Date).ToString('o')
    } | ConvertTo-Json | Set-Content -Path $stateFile -Encoding utf8

    powercfg /change standby-timeout-ac 0

    # No supported COM/cmdlet setter for active hours exists; this writes the same UX registry
    # keys the Settings app itself writes.
    Set-ItemProperty -Path 'HKLM:\SOFTWARE\Microsoft\WindowsUpdate\UX\Settings' -Name 'ActiveHoursStart' -Value $NightStart
    Set-ItemProperty -Path 'HKLM:\SOFTWARE\Microsoft\WindowsUpdate\UX\Settings' -Name 'ActiveHoursEnd' -Value $NightEnd

    Write-Host "Night power mode enabled. Previous values saved to $stateFile."
    exit 0
}

if ($Disable) {
    if (-not (Test-Path $stateFile)) {
        Write-Error "No saved state at $stateFile — nothing to restore. Run -Enable first."
        exit 1
    }
    $saved = Get-Content $stateFile -Raw -Encoding utf8 | ConvertFrom-Json

    if ($null -ne $saved.AcTimeoutMin) {
        powercfg /change standby-timeout-ac $saved.AcTimeoutMin
    }
    if ($null -ne $saved.ActiveHoursStart -and $null -ne $saved.ActiveHoursEnd) {
        Set-ItemProperty -Path 'HKLM:\SOFTWARE\Microsoft\WindowsUpdate\UX\Settings' -Name 'ActiveHoursStart' -Value $saved.ActiveHoursStart
        Set-ItemProperty -Path 'HKLM:\SOFTWARE\Microsoft\WindowsUpdate\UX\Settings' -Name 'ActiveHoursEnd' -Value $saved.ActiveHoursEnd
    }

    Remove-Item $stateFile -Force
    Write-Host "Night power mode disabled. Restored previous values."
    exit 0
}
