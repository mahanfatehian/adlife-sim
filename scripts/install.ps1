# AdLife installer for Windows (PowerShell).
#
# The Windows counterpart of scripts/install.sh, with the same contract: no
# privilege escalation, no shell-profile edits, uv is a requirement rather than
# something this script installs, ADLIFE_VERSION selects a version, and the
# script fails clearly when the package has not yet been published.
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File scripts\install.ps1
#   $env:ADLIFE_VERSION = "0.1.0"; powershell -File scripts\install.ps1

$ErrorActionPreference = "Stop"

function Deny([string]$Message, [int]$Code) {
    # A refusal prints its reason and exits with the documented code. Write-Error
    # alone would throw under Stop preference and exit 1 regardless of the code the
    # contract documents, so the diagnostic is written without terminating first.
    [Console]::Error.WriteLine($Message)
    exit $Code
}

$Package = "adlife-sim"
$Version = $env:ADLIFE_VERSION

if (-not $IsWindows -and $PSVersionTable.PSVersion.Major -ge 6) {
    Deny "AdLife supports this installer on Windows." 2
}

$Arch = $env:PROCESSOR_ARCHITECTURE
if ($Arch -ne "AMD64" -and $Arch -ne "ARM64") {
    Deny "Unsupported processor architecture: $Arch" 2
}

$Uv = Get-Command uv -ErrorAction SilentlyContinue
if (-not $Uv) {
    Deny "uv is required. Install it from https://docs.astral.sh/uv/ and rerun." 2
}

$Spec = $Package
if ($Version) {
    $Spec = "$Package==$Version"
}

$Installed = & uv tool install --upgrade $Spec
if ($LASTEXITCODE -ne 0) {
    Write-Output $Installed
    Deny "Installation failed. If the package has not been published yet, install from source: git clone https://github.com/mahanfatehian/adlife-sim.git; cd adlife-sim; uv sync" 1
}

Write-Output $Installed

$Adlife = Get-Command adlife -ErrorAction SilentlyContinue
if (-not $Adlife) {
    Deny "adlife is not on PATH after installation. Open a new terminal and run: adlife doctor --offline" 1
}

& adlife --version
if ($LASTEXITCODE -ne 0) { exit 1 }
& adlife doctor --offline
if ($LASTEXITCODE -ne 0) { exit 1 }

Write-Output "AdLife installed."
