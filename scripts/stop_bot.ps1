[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$pidPath = Join-Path $projectRoot ".run\bot.pid"
$pythonPath = Join-Path $projectRoot ".venv\Scripts\python.exe"

# Prevent concurrent start/stop scripts from racing over the PID file.
New-Item -ItemType Directory -Force -Path (Join-Path $projectRoot ".run") | Out-Null
$lockPath = Join-Path $projectRoot ".run\bot.windows.lock"
$operationLock = [System.IO.File]::Open(
    $lockPath, [System.IO.FileMode]::OpenOrCreate,
    [System.IO.FileAccess]::ReadWrite, [System.IO.FileShare]::None
)
try {
    if (-not (Test-Path -LiteralPath $pidPath)) {
        Write-Host "No bot PID file. The bot is not running through these scripts."
        exit 0
    }
    $savedText = (Get-Content -LiteralPath $pidPath -Raw).Trim()
    $botProcessId = 0
    if (-not [int]::TryParse($savedText, [ref]$botProcessId) -or $botProcessId -le 0) {
        throw "Invalid PID in $pidPath. No process was stopped."
    }
    $existing = Get-CimInstance Win32_Process -Filter "ProcessId = $botProcessId"
    if ($null -eq $existing) {
        Remove-Item -LiteralPath $pidPath
        Write-Host "Bot has already stopped. Stale PID file removed."
        exit 0
    }
    if ($existing.ExecutablePath -ine $pythonPath -or
        $existing.CommandLine -notmatch '(?:^|\s)-m\s+src\.main(?:\s|$)') {
        throw "PID $botProcessId belongs to another process. No process was stopped."
    }

    # Windows has no portable SIGTERM for a hidden console process.
    # Include child Python processes created by the Windows venv launcher.
    & taskkill.exe /PID $botProcessId /T /F | Out-Null
    if ($LASTEXITCODE -ne 0 -and (Get-Process -Id $botProcessId -ErrorAction SilentlyContinue)) {
        throw "Could not stop the bot process tree. PID file was retained."
    }
    Wait-Process -Id $botProcessId -Timeout 10 -ErrorAction SilentlyContinue
    if (Get-Process -Id $botProcessId -ErrorAction SilentlyContinue) {
        throw "Bot did not stop. PID file was retained."
    }
    Remove-Item -LiteralPath $pidPath
    Write-Host "Bot stopped. PID: $botProcessId. Logs were retained."
}
finally {
    $operationLock.Dispose()
}
