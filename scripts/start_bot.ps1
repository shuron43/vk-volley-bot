[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$runDirectory = Join-Path $projectRoot ".run"
$logDirectory = Join-Path $projectRoot "logs"
$pidPath = Join-Path $runDirectory "bot.pid"
$stdoutPath = Join-Path $logDirectory "bot.stdout.log"
$stderrPath = Join-Path $logDirectory "bot.stderr.log"

New-Item -ItemType Directory -Force -Path $runDirectory, $logDirectory | Out-Null

if (Test-Path -LiteralPath $pidPath) {
    $savedPid = [int](Get-Content -LiteralPath $pidPath -Raw)
    $existing = Get-Process -Id $savedPid -ErrorAction SilentlyContinue
    if ($null -ne $existing) {
        Write-Host "Бот уже запущен. PID: $savedPid"
        exit 0
    }
    Remove-Item -LiteralPath $pidPath
}

$process = Start-Process `
    -FilePath "uv" `
    -ArgumentList @("run", "--frozen", "python", "-m", "src.main") `
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
    Write-Error "Бот завершился при запуске. Проверьте logs/bot.stderr.log."
}

Write-Host "Бот запущен. PID: $($process.Id)"
Write-Host "stdout: $stdoutPath"
Write-Host "stderr: $stderrPath"
