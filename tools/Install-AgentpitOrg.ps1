#Requires -Version 7
#Requires -RunAsAdministrator
<#
.SYNOPSIS
Installs or updates PaceLine, PitBoard and PitCrew as organization (managed) plugins on Windows.

.DESCRIPTION
On a Team or Enterprise login, and on any machine with managed settings, the built-in
sec-default guard skips the hooks of plugins a person installs. A plugin counts as the
organization's when managed settings enable it from a directory marketplace at an absolute,
administrator-only path that lists it by a relative source. This script:

1. Fetches each plugin from github.com/agentpit-lab/agentpit at its release tag
   (<plugin>--v<version>, the latest unless -Version names one) and mirrors it into
   C:\Program Files\ClaudeCode\plugins\agentpit-org.
2. Writes that folder's marketplace.json under the name agentpit-org.
3. Writes the drop-in C:\Program Files\ClaudeCode\managed-settings.d\50-agentpit-org.json
   with extraKnownMarketplaces, enabledPlugins and appendPlugins. An existing
   managed-settings.json is left untouched.
4. Makes C:\Program Files\ClaudeCode and everything under it writable by administrators only.
5. Copies each plugin's data directory to the new plugin id, then disables the user-scope
   copies (<plugin>@agentpit) so the plugins do not load twice.

Run it again to update: it mirrors the latest release over the installed copy and keeps
every plugin already seated. A seated plugin is removed only when you ask: with -Remove, or
with a yes at the question -Plugins can raise.
Where the organization has server-managed settings, the admin console must carry
"managedSourcesBehavior": "merge", or Claude Code ignores this machine's plugin keys.
Needs git on PATH unless -Source is given.

.PARAMETER Version
Pins plugins to a release, as <plugin>=<version>: -Version pitboard=0.1.14.
For a plugin not named here, an interactive run offers its last ten releases with the
latest selected; a run without a console (scheduled, redirected) takes the latest.

.PARAMETER Plugins
The plugins to seat, from paceline, pitboard, pitcrew and stint; the default is
paceline, pitboard and pitcrew. Stint's module needs no seating, so it is left out by
default. A plugin already seated and left out is kept, or asked about in a console, never
removed silently. pwsh -File passes -Plugins paceline,pitboard as one string, which fails the name
check: run the script from a PowerShell 7 prompt, or through pwsh -Command, to name more
than one.

.PARAMETER Remove
Removes seated plugins and gives you your own copy back: -Remove stint. Naming a plugin in
-Remove and in -Plugins or -Version is an error; a name that is not seated changes nothing.
pwsh -File passes -Remove stint,pitcrew as one string, which fails the name check: run the
script from a PowerShell 7 prompt, or through pwsh -Command, to name more than one.

.PARAMETER Latest
Takes the latest release of every plugin not pinned by -Version, without asking anything,
the removal question included (seated plugins left out are kept).

.PARAMETER Source
A local marketplace folder that holds plugins\<name>, used for every plugin instead of
GitHub: for trying an unreleased build.

.PARAMETER Uninstall
Removes the drop-in and the folder, and re-enables the user-scope copies.

.EXAMPLE
pwsh -NoProfile -File .\Install-AgentpitOrg.ps1

.EXAMPLE
pwsh -NoProfile -File .\Install-AgentpitOrg.ps1 -Version pitboard=0.1.14

.EXAMPLE
pwsh -NoProfile -Command "& .\Install-AgentpitOrg.ps1 -Plugins paceline,pitboard"
#>
[CmdletBinding()]
param(
    [string[]]$Version = @(),
    [switch]$Latest,
    [string]$Source,
    # The known plugin list also appears in the second ValidateSet, in $known and in install-agentpit-org.sh.
    [ValidateSet('paceline', 'pitboard', 'pitcrew', 'stint')]
    [string[]]$Plugins = @('paceline', 'pitboard', 'pitcrew'),
    # Same list as the ValidateSet above; keep them and $known in step.
    [ValidateSet('paceline', 'pitboard', 'pitcrew', 'stint')]
    [string[]]$Remove = @(),
    [switch]$SkipUserPlugins,
    [switch]$Uninstall
)

$ErrorActionPreference = 'Stop'
# ValidateSet ignores case; tags, paths and ids do not.
$Plugins = @($Plugins | ForEach-Object ToLowerInvariant | Select-Object -Unique)
$Remove = @($Remove | ForEach-Object ToLowerInvariant | Select-Object -Unique)
# Same list as the two ValidateSets above and in install-agentpit-org.sh; keep them in step.
$known = @('paceline', 'pitboard', 'pitcrew', 'stint')

$marketplace = 'agentpit-org'
$userMarketplace = 'agentpit'
$repository = 'https://github.com/agentpit-lab/agentpit'
$root = 'C:\Program Files\ClaudeCode'
$target = Join-Path $root "plugins\$marketplace"
$dropIn = Join-Path $root "managed-settings.d\50-$marketplace.json"
$userHome = $env:USERPROFILE

function Invoke-Claude([string[]]$Arguments) {
    $output = & claude @Arguments 2>&1
    [pscustomobject]@{ ExitCode = $LASTEXITCODE; Output = ($output -join "`n") }
}

function Set-UserPlugins([string]$Verb, [string[]]$Names = $Plugins) {
    if ($SkipUserPlugins) { return }
    if (-not (Get-Command claude -ErrorAction SilentlyContinue)) {
        Write-Warning "claude is not on PATH; $Verb $($Names -join ', ') @$userMarketplace by hand."
        return
    }
    $listed = (Invoke-Claude @('plugin', 'list', '--json')).Output | ConvertFrom-Json
    foreach ($plugin in $Names) {
        $id = "$plugin@$userMarketplace"
        $entry = $listed | Where-Object { $_.id -eq $id -and $_.scope -eq 'user' }
        if (-not $entry) { continue }
        if (($Verb -eq 'disable') -ne [bool]$entry.enabled) { continue }
        $result = Invoke-Claude @('plugin', $Verb, $id, '--scope', 'user')
        if ($result.ExitCode -ne 0) { Write-Warning "claude plugin $Verb $id failed: $($result.Output)" }
        else { Write-Host "User-scope $id ${Verb}d." }
    }
}

function Set-AdminOnlyAcl([string]$Path) {
    # Administrators and SYSTEM full control, Users read and execute, nothing inherited.
    & icacls $Path /inheritance:r /grant:r '*S-1-5-32-544:(OI)(CI)F' '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-545:(OI)(CI)RX' /Q | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "icacls failed on $Path" }
    if (Get-ChildItem -LiteralPath $Path -Force) {
        & icacls (Join-Path $Path '*') /reset /T /C /Q | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "icacls /reset failed under $Path" }
    }
}

$pinned = @{}
foreach ($pin in $Version) {
    $name, $value = $pin -split '=', 2
    if (-not $value) { throw '-Version takes <plugin>=<version>' }
    $pinned[$name] = $value.TrimStart('v')
}

if ($PSBoundParameters.ContainsKey('Plugins')) {
    $clash = @($known | Where-Object { $_ -in $Remove -and $_ -in $Plugins })
    if ($clash) { throw "-Remove names a plugin that -Plugins seats: $($clash -join ', ')" }
}
$clash = @($known | Where-Object { $_ -in $Remove -and $pinned.ContainsKey($_) })
if ($clash) { throw "-Remove names a plugin that -Version pins: $($clash -join ', ')" }

if ($Uninstall) {
    if (Test-Path -LiteralPath $dropIn) { Remove-Item -LiteralPath $dropIn; Write-Host "Removed $dropIn" }
    if (Test-Path -LiteralPath $target) { Remove-Item -LiteralPath $target -Recurse; Write-Host "Removed $target" }
    Set-UserPlugins 'enable'
    Write-Host 'Restart Claude Code sessions to apply.'
    return
}

$interactive = -not $Latest -and [Environment]::UserInteractive -and
    -not [Console]::IsInputRedirected -and -not [Console]::IsOutputRedirected

# The plugins already seated are the ones the existing drop-in enables, read by pattern, line
# by line: the file is this script's own output (or the shell installer's), which puts each
# key and its true on one line.
$seated = @()
if ((Test-Path -LiteralPath $dropIn) -or (Get-Item -LiteralPath $dropIn -Force -ErrorAction SilentlyContinue)) {
    $cannotRead = "Cannot read $dropIn; fix or delete it, or run with -Uninstall."
    try {
        if (-not (Test-Path -LiteralPath $dropIn -PathType Leaf)) { throw 'not a file' }
        $lines = @(Get-Content -LiteralPath $dropIn -ErrorAction Stop)
    }
    catch { throw $cannotRead }
    $seen = @()
    foreach ($line in $lines) {
        foreach ($match in [regex]::Matches([string]$line, '"([a-z0-9-]+)@agentpit-org"\s*:\s*true')) {
            if ($match.Groups[1].Value -notin $seen) { $seen += $match.Groups[1].Value }
        }
    }
    $unknown = @($seen | Where-Object { $_ -notin $known })
    if ($unknown) { throw "$dropIn seats plugins this installer does not know: $($unknown -join ', '); fix or delete it, or run with -Uninstall." }
    $seated = @($known | Where-Object { $_ -in $seen })
}

# Requested: the plugins asked for (-Plugins or the default) minus -Remove, plus the pinned
# seated ones. Extra: seated, not requested, not removed. The final set is requested plus the
# extra the run keeps; it replaces $Plugins from here on.
$base = @($Plugins | Where-Object { $_ -notin $Remove })
$carry = @($known | Where-Object { $_ -in $seated -and $_ -notin $base -and $_ -notin $Remove })
$allowed = @($base) + @($carry)
foreach ($name in $pinned.Keys) {
    if ($name -notin $allowed) { throw "-Version takes <plugin>=<version> for one of: $($allowed -join ', ')" }
}
$requested = @($base) + @($known | Where-Object { $_ -in $carry -and $pinned.ContainsKey($_) })
$extra = @($known | Where-Object { $_ -in $seated -and $_ -notin $requested -and $_ -notin $Remove })
if (-not $requested -and -not $extra) { throw 'Nothing to seat: -Remove leaves no plugin. To remove them all, run with -Uninstall.' }

$dropping = @($known | Where-Object { $_ -in $seated -and $_ -in $Remove })
$kept = $extra
if ($extra -and $PSBoundParameters.ContainsKey('Plugins') -and $interactive) {
    Write-Host -NoNewline "Seated now but not in -Plugins: $($extra -join ', '). Remove them? Each gets your own copy back. [y/N] "
    $yes = [Console]::ReadKey($true).Key -eq 'Y'
    Write-Host $(if ($yes) { 'y' } else { 'N' })
    if ($yes) { $dropping = @($known | Where-Object { $_ -in $dropping -or $_ -in $extra }); $kept = @() }
}
$Plugins = @($requested) + @($known | Where-Object { $_ -in $kept })
if ($dropping) { Write-Host "Removing seated plugins: $($dropping -join ', ')." }
if ($kept) { Write-Host "Kept seated plugins outside this run's set: $($kept -join ', '). To remove one, run again with -Remove <name>." }

# The plugin's release versions, newest first.
function Get-ReleaseVersions([string]$Plugin) {
    $prefix = "refs/tags/$Plugin--v"
    $versions = & git ls-remote --tags --refs $repository "$Plugin--v*" |
        ForEach-Object { ($_ -split '\s+')[1] } |
        Where-Object { $_.StartsWith($prefix) } |
        ForEach-Object { $_.Substring($prefix.Length) } |
        Where-Object { $_ -match '^\d+\.\d+\.\d+$' }
    if ($LASTEXITCODE -ne 0) { throw "git ls-remote of $repository failed" }
    if (-not $versions) { throw "No release tag $Plugin--v<version> in $repository" }
    @($versions | Sort-Object { [version]$_ } -Descending)
}

# Up/Down moves, Enter picks, Esc keeps the latest.
function Select-Version([string]$Plugin, [string[]]$Versions) {
    $shown = @($Versions | Select-Object -First 10)
    $index = 0
    Write-Host "Choose the $Plugin release (Up/Down, Enter; Esc keeps the latest):"
    $draw = {
        for ($i = 0; $i -lt $shown.Count; $i++) {
            $label = if ($i -eq 0) { "$($shown[$i])  (latest)" } else { $shown[$i] }
            if ($i -eq $index) { Write-Host "  > `e[7m $label `e[0m`e[K" }
            else { Write-Host "     $label`e[K" }
        }
    }
    & $draw
    while ($true) {
        switch ([Console]::ReadKey($true).Key) {
            'UpArrow' { if ($index -gt 0) { $index-- } }
            'DownArrow' { if ($index -lt $shown.Count - 1) { $index++ } }
            'Home' { $index = 0 }
            'End' { $index = $shown.Count - 1 }
            'Enter' { return $shown[$index] }
            'Escape' { return $shown[0] }
        }
        Write-Host -NoNewline "`e[$($shown.Count)A"
        & $draw
    }
}

function Get-ReleaseTag([string]$Plugin) {
    if ($pinned.ContainsKey($Plugin)) { return "$Plugin--v$($pinned[$Plugin])" }
    # @() keeps a single release a list: a bare string would index to its first character.
    $versions = @(Get-ReleaseVersions $Plugin)
    $chosen = if ($interactive -and $versions.Count -gt 1) { Select-Version $Plugin $versions } else { $versions[0] }
    "$Plugin--v$chosen"
}

$work = Join-Path ([IO.Path]::GetTempPath()) "agentpit-org-$PID"

try {
    # Each plugin's own folder to copy from, and the marketplace.json beside it.
    $sources = @{}
    $tags = @{}
    foreach ($plugin in $Plugins) {
        if ($Source) {
            $sources[$plugin] = $Source
            continue
        }
        $tag = Get-ReleaseTag $plugin
        $checkout = Join-Path $work $plugin
        & git -c advice.detachedHead=false clone --depth 1 --quiet --branch $tag $repository $checkout
        if ($LASTEXITCODE -ne 0) { throw "git clone of $repository at $tag failed" }
        Write-Host "Fetched $tag"
        $sources[$plugin] = $checkout
        $tags[$plugin] = $tag
    }

    $entries = foreach ($plugin in $Plugins) {
        $from = $sources[$plugin]
        $pluginJson = Join-Path $from "plugins\$plugin\.claude-plugin\plugin.json"
        if (-not (Test-Path -LiteralPath $pluginJson)) { throw "No plugin '$plugin' in $from" }
        $pluginVersion = (Get-Content -LiteralPath $pluginJson -Raw | ConvertFrom-Json).version
        if ($tags[$plugin] -and $tags[$plugin] -ne "$plugin--v$pluginVersion") {
            Write-Warning "$($tags[$plugin]) holds $plugin $pluginVersion in its plugin.json"
        }
        $sourceManifest = Join-Path $from '.claude-plugin\marketplace.json'
        $description = $null
        if (Test-Path -LiteralPath $sourceManifest) {
            $description = ((Get-Content -LiteralPath $sourceManifest -Raw | ConvertFrom-Json).plugins |
                Where-Object name -eq $plugin).description
        }
        $entry = [ordered]@{ name = $plugin; source = "./plugins/$plugin" }
        if ($description) { $entry.description = $description }
        Write-Host "Source ${plugin}: $pluginVersion"
        $entry
    }

    New-Item -ItemType Directory -Force -Path (Join-Path $target 'plugins'), (Join-Path $target '.claude-plugin'), (Split-Path $dropIn) | Out-Null

    foreach ($plugin in $Plugins) {
        & robocopy (Join-Path $sources[$plugin] "plugins\$plugin") (Join-Path $target "plugins\$plugin") /MIR /XD .git /NFL /NDL /NJH /NJS /NP | Out-Null
        if ($LASTEXITCODE -ge 8) { throw "robocopy of $plugin failed with exit code $LASTEXITCODE" }
    }
    $removed = @(Get-ChildItem -LiteralPath (Join-Path $target 'plugins') -Directory | Where-Object Name -notin $Plugins)
    $removed | Remove-Item -Recurse

    $manifest = [ordered]@{
        name        = $marketplace
        owner       = [ordered]@{ name = 'agentpit-lab'; url = 'https://github.com/agentpit-lab' }
        description = 'AgentPit plugins seated as organization plugins from a local copy of the agentpit marketplace.'
        plugins     = @($entries)
    }
    $manifest | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $target '.claude-plugin\marketplace.json') -Encoding utf8NoBOM

    $ids = @($Plugins | ForEach-Object { "$_@$marketplace" })
    $enabled = [ordered]@{}
    foreach ($id in $ids) { $enabled[$id] = $true }
    $settings = [ordered]@{
        extraKnownMarketplaces = [ordered]@{
            $marketplace = [ordered]@{ source = [ordered]@{ source = 'directory'; path = $target } }
        }
        enabledPlugins         = $enabled
        appendPlugins          = $ids
    }
    $json = $settings | ConvertTo-Json -Depth 6
    $null = $json | ConvertFrom-Json   # invalid managed JSON stops Claude Code from starting
    # Write beside the drop-in, then rename: a truncated drop-in would drop seated plugins on the next rerun.
    $dropInTmp = "$dropIn.tmp"
    Set-Content -LiteralPath $dropInTmp -Value $json -Encoding utf8NoBOM
    Move-Item -LiteralPath $dropInTmp -Destination $dropIn -Force
    Write-Host "Wrote $dropIn"

    Set-AdminOnlyAcl $root
    Write-Host "$root is now writable by administrators only."

    # Plugin data lives under plugins\data\<plugin>-<marketplace>; carry it to the new id once.
    $dataRoot = Join-Path $userHome '.claude\plugins\data'
    foreach ($plugin in $Plugins) {
        $from = Join-Path $dataRoot "$plugin-$userMarketplace"
        $to = Join-Path $dataRoot "$plugin-$marketplace"
        if ((Test-Path -LiteralPath $from) -and -not (Test-Path -LiteralPath $to)) {
            Copy-Item -LiteralPath $from -Destination $to -Recurse
            Write-Host "Copied plugin data $from -> $to"
        }
    }

    Set-UserPlugins 'disable'
    # A plugin dropped from the set gets its user-scope copy back, which an earlier run disabled.
    if ($removed) { Set-UserPlugins 'enable' @($removed.Name) }
}
finally {
    if (Test-Path -LiteralPath $work) { Remove-Item -LiteralPath $work -Recurse -Force }
}

Write-Host ''
Write-Host 'Done. Restart Claude Code sessions, then check:'
Write-Host '  /status          -> Setting sources lists Enterprise managed settings'
Write-Host "  claude --debug   -> 'hooks module <plugin>@$marketplace loaded (... tier append)' and no 'bypassed by' line"
Write-Host '  claude plugin list does not show plugins loaded in place from a managed directory.'
