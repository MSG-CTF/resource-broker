param(
    [Parameter(Mandatory = $true)][string]$SourcePath,
    [Parameter(Mandatory = $true)][ValidatePattern('^[0-9a-f]{64}$')][string]$Sha256,
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
$tokenPath = Join-Path $programDir 'runtime-token'
$certPath = Join-Path $programDir 'server.crt'
$keyPath = Join-Path $programDir 'server.key'
$accountName = 'msgworker'
$accountDescription = 'MSG Windows Executor managed account'
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
if ($account -and $account.Description -ne $accountDescription) {
    throw 'Executor account name is already used by another account'
}
if (-not $task) {
    $random = New-Object byte[] 48
    $generator = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try { $generator.GetBytes($random) } finally { $generator.Dispose() }
    $password = 'Aa1!' + [Convert]::ToBase64String($random)
    $securePassword = ConvertTo-SecureString $password -AsPlainText -Force
    if ($account) {
        Set-LocalUser -Name $accountName -Password $securePassword
    } else {
        $account = New-LocalUser -Name $accountName -Password $securePassword -AccountNeverExpires -PasswordNeverExpires -UserMayNotChangePassword -Description $accountDescription
    }
}
if (-not $account) { throw 'Windows executor account is unavailable' }
if (-not $account.Enabled) { throw 'Windows executor account is disabled' }

$policyPrefix = Join-Path $env:TEMP ('msg-executor-rights-' + [guid]::NewGuid().ToString('N'))
$policyExport = "$policyPrefix-export.inf"
$policyTemplate = "$policyPrefix.inf"
$policyDatabase = "$policyPrefix.sdb"
try {
    & secedit.exe /export /cfg $policyExport /areas USER_RIGHTS /quiet | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Could not read batch logon policy' }
    $batchRight = @(Get-Content -LiteralPath $policyExport | Where-Object { $_ -match '^SeBatchLogonRight\s*=' })
    if ($batchRight.Count -ne 1) { throw 'Could not identify batch logon policy' }
    $members = @(($batchRight[0] -split '=', 2)[1].Trim() -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ })
    $workerSid = '*' + $account.SID.Value
    $workerNames = @($workerSid, $accountName, "$env:COMPUTERNAME\$accountName")
    if (@($members | Where-Object { $_ -in $workerNames }).Count -eq 0) {
        $lines = [string[]]@(
            '[Unicode]', 'Unicode=yes', '[Version]', 'signature="$CHICAGO$"',
            'Revision=1', '[Privilege Rights]', ('SeBatchLogonRight = ' + (($members + $workerSid) -join ','))
        )
        [IO.File]::WriteAllLines($policyTemplate, $lines, [Text.Encoding]::Unicode)
        & secedit.exe /validate $policyTemplate | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'Invalid batch logon policy template' }
        & secedit.exe /configure /db $policyDatabase /cfg $policyTemplate /overwrite /areas USER_RIGHTS /quiet | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'Could not grant executor batch logon right' }
        & secedit.exe /export /cfg $policyExport /areas USER_RIGHTS /quiet | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'Could not verify executor batch logon right' }
        $updated = @(Get-Content -LiteralPath $policyExport | Where-Object { $_ -match '^SeBatchLogonRight\s*=' })
        if ($updated.Count -ne 1) { throw 'Executor batch logon right was not applied' }
        $updatedMembers = @(($updated[0] -split '=', 2)[1].Trim() -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ })
        if (@($updatedMembers | Where-Object { $_ -in $workerNames }).Count -eq 0) {
            throw 'Executor batch logon right was not applied'
        }
    }
} finally {
    Remove-Item -LiteralPath $policyExport, $policyTemplate, $policyDatabase -ErrorAction SilentlyContinue
}

New-Item -ItemType Directory -Path $programDir, (Join-Path $dataDir 'jobs') -Force | Out-Null
Copy-Item -LiteralPath $source -Destination $target -Force
& $target -init-server -server-ip $PublicIP -tls-cert $certPath -tls-key $keyPath
if ($LASTEXITCODE -ne 0) { throw 'Could not initialize executor HTTPS certificate' }

$system = '*S-1-5-18:(OI)(CI)F'
$admins = '*S-1-5-32-544:(OI)(CI)F'
$executorRead = '*' + $account.SID.Value + ':(OI)(CI)RX'
$executorWrite = '*' + $account.SID.Value + ':(OI)(CI)M'
& icacls.exe $programDir /inheritance:r /grant:r $system $admins $executorRead | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Failed to set executor program permissions' }
& icacls.exe $keyPath /inheritance:r /grant:r '*S-1-5-18:F' '*S-1-5-32-544:F' | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Failed to protect executor TLS key' }
& icacls.exe $dataDir /inheritance:r /grant:r $system $admins $executorWrite | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Failed to set executor data permissions' }

if (-not $task) {
    $action = New-ScheduledTaskAction -Execute $target -Argument ('-root "' + $dataDir + '"')
    $settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 6)
    Register-ScheduledTask -TaskName $taskName -Action $action -Settings $settings -User "$env:COMPUTERNAME\$accountName" -Password $password -RunLevel Limited | Out-Null
}

$apiArguments = '-serve -root "' + $dataDir + '" -token-file "' + $tokenPath + '" -tls-cert "' + $certPath + '" -tls-key "' + $keyPath + '"'
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
if (Test-Path -LiteralPath $tokenPath) { Start-ScheduledTask -TaskName $apiTaskName }
