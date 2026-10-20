# Start the AdLife guided demo on this Windows computer (PowerShell).
#
# One command for the presenter: it asks once for the online AI key without
# showing it, keeps the key in this process's environment only while the server
# runs, starts the workbench with the online AI address approved, opens the
# guided start page in the default browser, and removes the key from the
# environment when the server stops. The key is never a command-line argument
# and never written to disk. Press Enter at the prompt, or pass -PracticeOnly,
# to start without a key: practice answers then work, the online AI does not.
#
# Usage, from the repository folder after `uv sync`:
#   powershell -ExecutionPolicy Bypass -File scripts\start-guided-demo.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\start-guided-demo.ps1 -Port 48080
#   powershell -ExecutionPolicy Bypass -File scripts\start-guided-demo.ps1 -PracticeOnly -NoBrowser
#
# Another OpenAI-compatible service works the same way, for example:
#   ... -ProviderName myai -ProviderUrl https://api.example.com/v1 -KeyVariable MYAI_API_KEY
#
# Press Ctrl+C in this window to stop the server.

param(
    [ValidateRange(1024, 65535)][int]$Port = 50050,
    [string]$Workspace = ".\demo-guided",
    [ValidatePattern("^[a-z0-9][a-z0-9-]{0,63}$")][string]$ProviderName = "gapgpt",
    [ValidatePattern("^https://")][string]$ProviderUrl = "https://api.gapgpt.app/v1",
    [ValidatePattern("^[A-Z_][A-Z0-9_]{0,127}$")][string]$KeyVariable = "GAPGPT_API_KEY",
    [switch]$PracticeOnly,
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"

function Deny([string]$Message, [int]$Code) {
    # Same refusal contract as scripts/install.ps1: print the reason, exit with the code.
    [Console]::Error.WriteLine($Message)
    exit $Code
}

function Format-Argument([string]$Value) {
    # Start-Process joins its argument list with spaces, so quote any value that has one.
    if ($Value -match '\s') { return '"' + $Value + '"' }
    return $Value
}

if (-not $IsWindows -and $PSVersionTable.PSVersion.Major -ge 6) {
    Deny "This start script is for Windows. Elsewhere, run: uv run adlife city-workbench --help" 2
}

$Uv = Get-Command uv -ErrorAction SilentlyContinue
if (-not $Uv) {
    Deny "uv is required. Install it from https://docs.astral.sh/uv/, run 'uv sync' in this folder, and rerun." 2
}

$Busy = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($Busy) {
    Deny "Port $Port is already in use. Stop the other server (Ctrl+C in its window) or rerun with -Port 48080." 2
}

$Address = "http://127.0.0.1:$Port/guide"
$Arguments = @(
    "run", "adlife", "city-workbench",
    "--workspace", $Workspace,
    "--port", "$Port",
    "--provider-origin", "$ProviderName=$ProviderUrl"
)

$HasKey = $false
if (-not $PracticeOnly) {
    # A key already set in this window (as the manual start in the guide does) is used when
    # the prompt is left empty.
    $Preset = -not [string]::IsNullOrEmpty([Environment]::GetEnvironmentVariable($KeyVariable, "Process"))
    $Prompt = "$ProviderName API key (input hidden; press Enter for practice answers only)"
    if ($Preset) {
        $Prompt = "$ProviderName API key (input hidden; press Enter to use the key already in $KeyVariable)"
    }
    $Secure = Read-Host $Prompt -AsSecureString
    if ($Secure.Length -gt 0) {
        [Environment]::SetEnvironmentVariable(
            $KeyVariable,
            [System.Net.NetworkCredential]::new("", $Secure).Password,
            "Process"
        )
        $HasKey = $true
    } elseif ($Preset) {
        $HasKey = $true
    }
    $Secure.Dispose()
    if ($HasKey) {
        $Arguments += @("--provider-key-env", "$ProviderName=$KeyVariable")
    }
}

if ($HasKey) {
    Write-Output "Online AI: $ProviderName at $ProviderUrl; the key stays in this window's process only."
} else {
    Write-Output "No key given: practice answers work; the online AI will ask for a key."
}
Write-Output "Starting the server on $Address (Ctrl+C stops it)..."

$Server = $null
try {
    $Server = Start-Process -FilePath $Uv.Source -ArgumentList ($Arguments | ForEach-Object { Format-Argument $_ }) -NoNewWindow -PassThru
    $Ready = $false
    $Deadline = (Get-Date).AddSeconds(90)
    while (-not $Ready -and -not $Server.HasExited -and (Get-Date) -lt $Deadline) {
        try {
            $Probe = Invoke-WebRequest -Uri $Address -UseBasicParsing -TimeoutSec 2
            $Ready = $Probe.StatusCode -eq 200
        } catch {
            Start-Sleep -Milliseconds 500
        }
    }
    if (-not $Ready) {
        if ($Server.HasExited) {
            Deny "The server stopped before it was ready; read its message above." 1
        }
        Deny "The server did not answer on $Address within 90 seconds." 1
    }
    Write-Output "Ready: $Address"
    if (-not $NoBrowser) {
        Start-Process $Address
    }
    $Server.WaitForExit()
} finally {
    # The key leaves this process's environment however the server ends.
    [Environment]::SetEnvironmentVariable($KeyVariable, $null, "Process")
    if ($null -ne $Server -and -not $Server.HasExited) {
        & taskkill.exe /PID $Server.Id /T /F | Out-Null
    }
}
