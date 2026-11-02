param(
    [Parameter(Mandatory = $true)][string]$SourcePath,
    [Parameter(Mandatory = $true)][ValidatePattern('^[0-9a-f]{64}$')][string]$Sha256,
    [Parameter(Mandatory = $true)][string]$RuntimeClientCAPath,
    [Parameter(Mandatory = $true)][string]$PublicIP
)

$ErrorActionPreference = 'Stop'
$source = (Resolve-Path -LiteralPath $SourcePath).Path
if ((Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash.ToLowerInvariant() -ne $Sha256) {
    throw 'Windows executor checksum mismatch'
}

$programDir = Join-Path $env:ProgramFiles 'MSG\windows-executor'
$dataDir = Join-Path $env:ProgramData 'MSG\windows-executor'
$target = Join-Path $programDir 'windows-executor.exe'
$taskName = 'MSGWindowsExecutor'
$apiTaskName = 'MSGWindowsExecutorAPI'
$clientCAPath = Join-Path $programDir 'runtime-client-ca.pem'
$privateDir = Join-Path $programDir 'private'
$certPath = Join-Path $privateDir 'server.crt'
$keyPath = Join-Path $privateDir 'server.key'
$accountName = 'ctfuser'
$task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($task -and $task.State -eq 'Running') { throw 'Windows executor task is running' }
$apiTask = Get-ScheduledTask -TaskName $apiTaskName -ErrorAction SilentlyContinue
if ($apiTask -and $apiTask.State -eq 'Running') {
    Stop-ScheduledTask -TaskName $apiTaskName
    for ($i = 0; $i -lt 10 -and (Get-ScheduledTask -TaskName $apiTaskName).State -eq 'Running'; $i++) {
        Start-Sleep -Seconds 1
    }
    if ((Get-ScheduledTask -TaskName $apiTaskName).State -eq 'Running') { throw 'Executor API task did not stop' }
}

$account = Get-LocalUser -Name $accountName -ErrorAction SilentlyContinue
if (-not $account) { throw 'Windows executor account is unavailable' }
if (-not $account.Enabled) { throw 'Windows executor account is disabled' }
if (-not (Test-Path -LiteralPath 'C:\ctf\run-payload.ps1' -PathType Leaf)) { throw 'Challenge runner is missing' }
if ((Get-LocalGroupMember -SID 'S-1-5-32-544').SID -contains $account.SID) { throw 'ctfuser is an administrator' }
$random = New-Object byte[] 48
$generator = [Security.Cryptography.RandomNumberGenerator]::Create()
try { $generator.GetBytes($random) } finally { $generator.Dispose() }
$password = 'Aa1!' + [Convert]::ToBase64String($random)
Set-LocalUser -Name $accountName -Password (ConvertTo-SecureString $password -AsPlainText -Force)

$policyPrefix = Join-Path $env:TEMP ('msg-executor-rights-' + [guid]::NewGuid().ToString('N'))
$policyExport = "$policyPrefix-export.inf"
$policyTemplate = "$policyPrefix.inf"
$policyDatabase = "$policyPrefix.sdb"
try {
    & secedit.exe /export /cfg $policyExport /areas USER_RIGHTS /quiet | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Could not read batch logon policy' }
    $batchRight = @(Get-Content -LiteralPath $policyExport | Where-Object { $_ -match '^SeBatchLogonRight\s*=' })
    if ($batchRight.Count -ne 1) { throw 'Could not identify batch logon policy' }
    $workerSid = '*' + $account.SID.Value
    $workerNames = @($workerSid, $accountName, "$env:COMPUTERNAME\$accountName")
    $rights = @('SeBatchLogonRight', 'SeDenyInteractiveLogonRight', 'SeDenyRemoteInteractiveLogonRight', 'SeDenyNetworkLogonRight')
    $policyLines = @('[Unicode]', 'Unicode=yes', '[Version]', 'signature="$CHICAGO$"', 'Revision=1', '[Privilege Rights]')
    foreach ($right in $rights) {
        $row = @(Get-Content -LiteralPath $policyExport | Where-Object { $_ -match "^$right\s*=" })
        [string[]]$current = if ($row.Count) { @(($row[0] -split '=', 2)[1].Trim() -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ }) } else { @() }
        if (@($current | Where-Object { $_ -in $workerNames }).Count -eq 0) { $current += $workerSid }
        $policyLines += ($right + ' = ' + ($current -join ','))
    }
    [IO.File]::WriteAllLines($policyTemplate, [string[]]$policyLines, [Text.Encoding]::Unicode)
    & secedit.exe /validate $policyTemplate | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Invalid executor logon policy template' }
    & secedit.exe /configure /db $policyDatabase /cfg $policyTemplate /overwrite /areas USER_RIGHTS /quiet | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Could not configure executor logon rights' }
    & secedit.exe /export /cfg $policyExport /areas USER_RIGHTS /quiet | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Could not verify executor logon rights' }
    foreach ($right in $rights) {
        $row = @(Get-Content -LiteralPath $policyExport | Where-Object { $_ -match "^$right\s*=" })
        $updated = if ($row.Count -eq 1) { @(($row[0] -split '=', 2)[1].Trim() -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ }) } else { @() }
        if (@($updated | Where-Object { $_ -in $workerNames }).Count -eq 0) { throw "Executor right was not applied: $right" }
    }
} finally {
    Remove-Item -LiteralPath $policyExport, $policyTemplate, $policyDatabase -ErrorAction SilentlyContinue
}

New-Item -ItemType Directory -Path $programDir, $privateDir, (Join-Path $dataDir 'jobs') -Force | Out-Null
Copy-Item -LiteralPath $source -Destination $target -Force
$RuntimeClientCAPem = Get-Content -LiteralPath $RuntimeClientCAPath -Raw
if ($RuntimeClientCAPem -notmatch '^-----BEGIN CERTIFICATE-----' -or $RuntimeClientCAPem -notmatch '-----END CERTIFICATE-----\s*$') {
    throw 'Runtime client CA must be a PEM certificate'
}
[IO.File]::WriteAllText($clientCAPath, $RuntimeClientCAPem, [Text.Encoding]::ASCII)

$system = '*S-1-5-18:(OI)(CI)F'
$admins = '*S-1-5-32-544:(OI)(CI)F'
$executorRead = '*' + $account.SID.Value + ':(OI)(CI)RX'
$executorWrite = '*' + $account.SID.Value + ':(OI)(CI)M'
& icacls.exe $programDir /inheritance:r /grant:r $system $admins $executorRead | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Failed to set executor program permissions' }
& icacls.exe $privateDir /inheritance:r /grant:r $system $admins | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Failed to protect executor TLS files' }
& icacls.exe $dataDir /inheritance:r /grant:r $system $admins $executorWrite | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Failed to set executor data permissions' }
& $target -init-server -server-ip $PublicIP -tls-cert $certPath -tls-key $keyPath
if ($LASTEXITCODE -ne 0) { throw 'Could not initialize executor HTTPS certificate' }

$action = New-ScheduledTaskAction -Execute $target -Argument ('-root "' + $dataDir + '"')
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 6)
Register-ScheduledTask -TaskName $taskName -Action $action -Settings $settings -User "$env:COMPUTERNAME\$accountName" -Password $password -RunLevel Limited -Force | Out-Null
Remove-Variable password

$apiArguments = '-serve -root "' + $dataDir + '" -client-ca "' + $clientCAPath + '" -tls-cert "' + $certPath + '" -tls-key "' + $keyPath + '"'
$apiAction = New-ScheduledTaskAction -Execute $target -Argument $apiArguments
$apiTrigger = New-ScheduledTaskTrigger -AtStartup
$apiSettings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName $apiTaskName -Action $apiAction -Trigger $apiTrigger -Settings $apiSettings -User 'SYSTEM' -RunLevel Highest -Force | Out-Null
$firewall = Get-NetFirewallRule -DisplayName 'MSG Windows Executor HTTPS' -ErrorAction SilentlyContinue
if (-not $firewall) {
    New-NetFirewallRule -DisplayName 'MSG Windows Executor HTTPS' -Direction Inbound -Action Allow -Protocol TCP -LocalPort 443 | Out-Null
}

if ((Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash.ToLowerInvariant() -ne $Sha256) {
    throw 'Installed Windows executor checksum mismatch'
}
& $target -version
if ($LASTEXITCODE -ne 0) { throw 'Installed Windows executor did not start' }
Start-ScheduledTask -TaskName $apiTaskName
for ($i = 0; $i -lt 30; $i++) {
    if ((Get-ScheduledTask -TaskName $apiTaskName).State -eq 'Running' -and
        (Test-Path -LiteralPath $certPath -PathType Leaf) -and
        (Get-NetTCPConnection -LocalPort 443 -State Listen -ErrorAction SilentlyContinue)) {
        Write-Output 'EXECUTOR_INSTALL_OK'
        exit 0
    }
    Start-Sleep -Seconds 1
}
throw 'Windows executor API did not start'
