$ErrorActionPreference = 'Stop'
$taskRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
Set-Location -LiteralPath $taskRoot
$taskPidPath = Join-Path $taskRoot 'data\bot.pid'
$taskPython = (Get-Command python -ErrorAction Stop).Source
if (Test-Path -LiteralPath $taskPidPath) {
    $taskBotId = [int](Get-Content -LiteralPath $taskPidPath)
    $taskExisting = Get-CimInstance Win32_Process -Filter "ProcessId = $taskBotId"
    if ($taskExisting) {
        if ($taskExisting.ExecutablePath -ne $taskPython -or $taskExisting.CommandLine -notmatch '\s-m\s+wishlist\.bot(?:\s|$)') {
            throw 'Saved PID belongs to a different process. Refusing to stop it.'
        }
        Stop-Process -Id $taskBotId
        Wait-Process -Id $taskBotId -ErrorAction SilentlyContinue
    }
}
New-Item -ItemType Directory -Force -Path (Join-Path $taskRoot 'data') | Out-Null
if (Test-Path -LiteralPath (Join-Path $taskRoot 'data\wishlist.sqlite3')) {
    & $taskPython -m wishlist.backup
    if ($LASTEXITCODE -ne 0) { throw 'Backup failed. Bot has not been restarted.' }
}
$taskProcess = Start-Process -FilePath $taskPython -ArgumentList '-m','wishlist.bot' -WorkingDirectory $taskRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $taskRoot 'data\bot.stdout.log') -RedirectStandardError (Join-Path $taskRoot 'data\bot.stderr.log') -PassThru
Set-Content -LiteralPath $taskPidPath -Value $taskProcess.Id
Write-Output "Bot process ID: $($taskProcess.Id)"
