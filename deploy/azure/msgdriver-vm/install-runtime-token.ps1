param(
    [Parameter(Mandatory = $true)][string]$RuntimeToken
)

$ErrorActionPreference = 'Stop'
if ($RuntimeToken.Length -lt 32 -or $RuntimeToken.Length -gt 1024 -or $RuntimeToken -cmatch '[^\x21-\x7E]') {
    throw 'Invalid Runtime token format'
}
$programDir = Join-Path $env:ProgramFiles 'MSG\windows-executor'
$tokenPath = Join-Path $programDir 'runtime-token'
$taskName = 'MSGWindowsExecutorAPI'
if (-not (Test-Path -LiteralPath (Join-Path $programDir 'windows-executor.exe'))) {
    throw 'Install the Windows executor with Terraform first'
}

$task = Get-ScheduledTask -TaskName $taskName -ErrorAction Stop
if ($task.State -eq 'Running') {
    Stop-ScheduledTask -TaskName $taskName
    for ($i = 0; $i -lt 10 -and (Get-ScheduledTask -TaskName $taskName).State -eq 'Running'; $i++) {
        Start-Sleep -Seconds 1
    }
    if ((Get-ScheduledTask -TaskName $taskName).State -eq 'Running') { throw 'Executor API task did not stop' }
}
New-Item -ItemType File -Path $tokenPath -Force | Out-Null
& icacls.exe $tokenPath /inheritance:r /grant:r '*S-1-5-18:F' '*S-1-5-32-544:F' | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Could not protect Runtime token file' }
[IO.File]::WriteAllText($tokenPath, $RuntimeToken, [Text.Encoding]::ASCII)
Start-ScheduledTask -TaskName $taskName
for ($i = 0; $i -lt 10; $i++) {
    Start-Sleep -Seconds 1
    if ((Get-ScheduledTask -TaskName $taskName).State -eq 'Running' -and
        (Get-NetTCPConnection -LocalPort 443 -State Listen -ErrorAction SilentlyContinue)) {
        Write-Output 'Windows executor API is running'
        exit 0
    }
}
throw 'Windows executor API did not start'
