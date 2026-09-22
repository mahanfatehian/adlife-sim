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

$Package = "adlife-sim"
$Version = $env:ADLIFE_VERSION

if (-not $IsWindows -and $PSVersionTable.PSVersion.Major -ge 6) {
    Write-Error "AdLife supports this installer on Windows."
    exit 2
}

$Arch = $env:PROCESSOR_ARCHITECTURE
if ($Arch -ne "AMD64" -and $Arch -ne "ARM64") {
    Write-Error "Unsupported processor architecture: $Arch"
    exit 2
}

$Uv = Get-Command uv -ErrorAction SilentlyContinue
if (-not $Uv) {
    Write-Error "uv is required. Install it from https://docs.astral.sh/uv/ and rerun."
    exit 2
}

$Spec = $Package
if ($Version) {
    $Spec = "$Package==$Version"
}

$Installed = & uv tool install --upgrade $Spec
if ($LASTEXITCODE -ne 0) {
    Write-Host $Installed
    Write-Error "Installation failed. If the package has not been published yet, install from source: git clone https://github.com/mahanfatehian/adlife-sim.git; cd adlife-sim; uv sync"
    exit 1
}

Write-Host $Installed

$Adlife = Get-Command adlife -ErrorAction SilentlyContinue
if (-not $Adlife) {
    Write-Error "adlife is not on PATH after installation. Open a new terminal and run: adlife doctor --offline"
    exit 1
}

& adlife --version
if ($LASTEXITCODE -ne 0) { exit 1 }
& adlife doctor --offline
if ($LASTEXITCODE -ne 0) { exit 1 }

Write-Host "AdLife installed."
