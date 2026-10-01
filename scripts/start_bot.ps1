[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$runDirectory = Join-Path $projectRoot ".run"
$logDirectory = Join-Path $projectRoot "logs"
$pidPath = Join-Path $runDirectory "bot.pid"
$pythonPath = Join-Path $projectRoot ".venv\Scripts\python.exe"
$stdoutPath = Join-Path $logDirectory "bot.stdout.log"
$stderrPath = Join-Path $logDirectory "bot.stderr.log"

New-Item -ItemType Directory -Force -Path $runDirectory, $logDirectory | Out-Null

# Prevent concurrent start/stop scripts from racing over the PID file.
New-Item -ItemType Directory -Force -Path (Join-Path $projectRoot ".run") | Out-Null
$lockPath = Join-Path $projectRoot ".run\bot.windows.lock"
$operationLock = [System.IO.File]::Open(
    $lockPath, [System.IO.FileMode]::OpenOrCreate,
    [System.IO.FileAccess]::ReadWrite, [System.IO.FileShare]::None
)
try {
    if (Test-Path -LiteralPath $pidPath) {
        $savedText = (Get-Content -LiteralPath $pidPath -Raw).Trim()
        $botProcessId = 0
        if (-not [int]::TryParse($savedText, [ref]$botProcessId) -or $botProcessId -le 0) {
            throw "Invalid PID in $pidPath. Inspect the file before removing it."
        }
        $existing = Get-CimInstance Win32_Process -Filter "ProcessId = $botProcessId"
        if ($null -ne $existing) {
            if ($existing.ExecutablePath -ine $pythonPath -or
                $existing.CommandLine -notmatch '(?:^|\s)-m\s+src\.main(?:\s|$)') {
                throw "PID $botProcessId belongs to another process. Nothing was started."
            }
            Write-Host "Bot is already running. PID: $botProcessId"
            exit 0
        }
        Remove-Item -LiteralPath $pidPath
    }

    # Synchronize first; track Python (including a possible venv launcher), not uv.
    Push-Location -LiteralPath $projectRoot
    try {
        uv sync --frozen
        if ($LASTEXITCODE -ne 0) {
            throw "uv sync failed. The bot was not started."
        }
    }
    finally {
        Pop-Location
    }

    $process = Start-Process `
        -FilePath $pythonPath `
        -ArgumentList @("-u", "-X", "utf8", "-m", "src.main") `
        -WorkingDirectory $projectRoot `
        -RedirectStandardOutput $stdoutPath `
        -RedirectStandardError $stderrPath `
        -WindowStyle Hidden `
        -PassThru

    Set-Content -LiteralPath $pidPath -Value $process.Id -Encoding ascii
    Start-Sleep -Seconds 2
    $process.Refresh()
    if ($process.HasExited) {
        Remove-Item -LiteralPath $pidPath -ErrorAction SilentlyContinue
        throw "Bot exited during startup. Check $stderrPath"
    }
    Write-Host "Bot started. PID: $($process.Id)"
    Write-Host "stdout: $stdoutPath"
    Write-Host "stderr: $stderrPath"
}
finally {
    $operationLock.Dispose()
}
