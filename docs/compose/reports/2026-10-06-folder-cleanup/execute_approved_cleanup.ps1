param([switch]$VerifyOnly)
if ($VerifyOnly) {
    $auditRoot = 'D:\~Vibe-Coding\Projects\Fmodel_Edited_UEformat'
    $auditEvidence = Join-Path $auditRoot 'docs\current\evidence\2026-10-06\folder-cleanup-execution'
    $queue = [Collections.Generic.Stack[string]]::new()
    $queue.Push($auditRoot)
    $totals = @{}
    $errors = [Collections.Generic.List[object]]::new()
    $links = [Collections.Generic.List[string]]::new()
    $fileCount = 0
    $logicalBytes = [long]0
    while ($queue.Count -gt 0) {
        $current = $queue.Pop()
        try {
            $items = @(Get-ChildItem -LiteralPath $current -Force -ErrorAction Stop)
            foreach ($item in $items) {
                if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { $links.Add($item.FullName); continue }
                if ($item.PSIsContainer) { $queue.Push($item.FullName) }
                else {
                    $relative = $item.FullName.Substring($auditRoot.Length + 1)
                    $top = ($relative -split '\\')[0]
                    if (!$totals.ContainsKey($top)) { $totals[$top] = [long]0 }
                    $totals[$top] += [long]$item.Length
                    $logicalBytes += [long]$item.Length
                    $fileCount++
                }
            }
        } catch { $errors.Add([pscustomobject]@{ path=$current; error=$_.Exception.Message }) }
    }
    $receipt = [pscustomobject]@{
        result='PASS_READABLE_INVENTORY'; scope='Same approved process identity as cleanup; logical readable ordinary files only; no link traversal'
        scannedAt=[DateTimeOffset]::Now.ToString('o'); bytes=$logicalBytes; GiB=[math]::Round($logicalBytes/1GB,3); fileCount=$fileCount
        topLevelBytes=$totals; unreadablePaths=@($errors); skippedReparsePoints=@($links)
    }
    $receipt | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath (Join-Path $auditEvidence 'after-storage-inventory.json') -Encoding utf8
    $humanReportPath = Join-Path $auditRoot 'docs/current/FOLDER_CLEANUP_EXECUTION_2026-10-06.md'
    $addition = "`n## 清理后占用复核`n`n同一执行身份扫描得到可读普通文件约 $($receipt.GiB) GiB，共 $fileCount 个文件。统计不含无法读取区域，不遍历 reparse point。完整目录占用见 after-storage-inventory.json。`n"
    Add-Content -LiteralPath $humanReportPath -Value $addition -Encoding utf8
    $receipt | ConvertTo-Json -Depth 10 | Write-Output
    exit 0
}

# User-authorized phase 1/2 cleanup. Requires approved access to the D: workspace.
# Every removal stays in native PowerShell and uses a resolved, validated LiteralPath.
$ErrorActionPreference = 'Stop'
$workspaceRoot = 'D:\~Vibe-Coding\Projects\Fmodel_Edited_UEformat'
$workspacePrefix = $workspaceRoot + '\'
$evidenceRoot = Join-Path $workspaceRoot 'docs\current\evidence\2026-10-06\folder-cleanup-execution'
$planPath = Join-Path $evidenceRoot 'execution-plan.json'
$snapshotPath = Join-Path $evidenceRoot 'before-snapshot.json'
if ((Get-FileHash -LiteralPath $planPath -Algorithm SHA256).Hash.ToLowerInvariant() -ne 'b16f5e0030033dff306e1e1331de316ad710c220ae6d70b82ad81f06e1849b2f') { throw 'Reviewed plan changed' }
if ((Get-FileHash -LiteralPath $snapshotPath -Algorithm SHA256).Hash.ToLowerInvariant() -ne '18b251726e915d041f6a64008fc0dac53e2a0cb8147dd4f9f413e59253e2e8da') { throw 'Reviewed snapshot changed' }
$plan = Get-Content -LiteralPath $planPath -Raw -Encoding utf8 | ConvertFrom-Json
$before = Get-Content -LiteralPath $snapshotPath -Raw -Encoding utf8 | ConvertFrom-Json
if ($plan.root -ne $workspaceRoot -or @($plan.phase1).Count -ne 107 -or @($plan.phase2).Count -ne 6) { throw 'Unexpected cleanup scope' }
$results = [Collections.Generic.List[object]]::new()
$driveFreeBefore = [IO.DriveInfo]::new('D:\').AvailableFreeSpace

function Assert-Target([string]$Target) {
    $absolute = [IO.Path]::GetFullPath($Target).TrimEnd('\')
    if (!$absolute.StartsWith($workspacePrefix, [StringComparison]::OrdinalIgnoreCase)) { throw "Outside workspace: $absolute" }
    $resolved = (Resolve-Path -LiteralPath $absolute -ErrorAction Stop).ProviderPath.TrimEnd('\')
    if (!$resolved.Equals($absolute, [StringComparison]::OrdinalIgnoreCase)) { throw "Resolved target differs: $absolute" }
    $cursor = $absolute
    while ($true) {
        $item = Get-Item -LiteralPath $cursor -Force -ErrorAction Stop
        if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw "Reparse point: $cursor" }
        if ($cursor.Equals($workspaceRoot, [StringComparison]::OrdinalIgnoreCase)) { break }
        $cursor = [IO.Path]::GetDirectoryName($cursor)
        if (!$cursor) { throw 'Target did not reach approved workspace' }
    }
    return $absolute
}

function Read-Tree([string]$Target) {
    $absolute = Assert-Target $Target
    $files = [Collections.Generic.Dictionary[string,object]]::new([StringComparer]::OrdinalIgnoreCase)
    $stack = [Collections.Generic.Stack[string]]::new()
    $stack.Push($absolute)
    $epoch = [DateTime]::new(1970,1,1,0,0,0,[DateTimeKind]::Utc)
    while ($stack.Count -gt 0) {
        foreach ($item in Get-ChildItem -LiteralPath $stack.Pop() -Force -ErrorAction Stop) {
            if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw "Reparse point inside target: $($item.FullName)" }
            if ($item.PSIsContainer) { $stack.Push($item.FullName) }
            else {
                $relative = $item.FullName.Substring($workspacePrefix.Length).Replace('\','/')
                $files[$relative] = [pscustomobject]@{ bytes=[long]$item.Length; mtime=$item.LastWriteTimeUtc.Subtract($epoch).TotalSeconds }
            }
        }
    }
    return [pscustomobject]@{ files=$files; bytes=[long](($files.Values | Measure-Object -Property bytes -Sum).Sum) }
}

function Assert-Snapshot($Row) {
    $expectedPath = [IO.Path]::GetFullPath((Join-Path $workspaceRoot $Row.path))
    if (!$expectedPath.Equals($Row.absolutePath, [StringComparison]::OrdinalIgnoreCase)) { throw 'Plan target mismatch' }
    $actual = Read-Tree $expectedPath
    $expected = @($Row.current.files.PSObject.Properties)
    if ($actual.files.Count -ne $expected.Count -or $actual.bytes -ne $Row.current.bytes) { throw "Contents changed: $($Row.path)" }
    foreach ($file in $expected) {
        if (!$actual.files.ContainsKey($file.Name)) { throw "File missing: $($file.Name)" }
        $current = $actual.files[$file.Name]
        if ($current.bytes -ne $file.Value.bytes -or [math]::Abs($current.mtime - $file.Value.mtime) -gt 0.002) { throw "File changed: $($file.Name)" }
    }
}

function Assert-Protections {
    foreach ($file in $before.protectedFiles) {
        $path = Join-Path $workspaceRoot $file.path
        if ((Get-Item -LiteralPath $path -Force).Length -ne $file.bytes -or (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() -ne $file.sha256) { throw "Protected file changed: $($file.path)" }
    }
    foreach ($repo in $before.repositories) {
        $path = Join-Path $workspaceRoot $repo.path
        $head = ((& git -C $path rev-parse HEAD) | Out-String).Trim()
        if ($LASTEXITCODE -ne 0 -or $head -ne $repo.head) { throw "Repository HEAD changed: $($repo.path)" }
        $status = ((& git -C $path status --porcelain) | Out-String).Trim()
        if ($LASTEXITCODE -ne 0 -or $status.Replace("`r`n","`n") -ne $repo.status.Replace("`r`n","`n")) { throw "Repository status changed: $($repo.path)" }
        $worktrees = ((& git -C $path worktree list --porcelain) | Out-String).Trim()
        if ($LASTEXITCODE -ne 0 -or $worktrees.Replace("`r`n","`n") -ne $repo.worktrees.Replace("`r`n","`n")) { throw "Worktree registration changed: $($repo.path)" }
    }
}

Assert-Protections
foreach ($row in @($before.phase1) + @($before.phase2)) { Assert-Snapshot $row }
$tracked = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
foreach ($repo in $before.repositories) {
    $names = ((& git -C (Join-Path $workspaceRoot $repo.path) ls-files -z) | Out-String)
    if ($LASTEXITCODE -ne 0) { throw 'Git tracked-file read failed' }
    foreach ($name in $names.Split([char]0)) {
        $name = $name.TrimEnd("`r","`n")
        if ($name) { $null = $tracked.Add($repo.path + '/' + $name.Replace('\','/')) }
    }
}
foreach ($row in @($before.phase1) + @($before.phase2)) {
    foreach ($file in $row.current.files.PSObject.Properties) {
        if ($tracked.Contains($file.Name)) { throw "Tracked source inside target: $($file.Name)" }
    }
    if ($row.path -match '(^|/)(CUE4Parse-Natives|\.git)(/|$)') { throw 'Native/Git target forbidden' }
}
foreach ($process in Get-CimInstance Win32_Process) {
    if (!$process.ExecutablePath) { continue }
    foreach ($row in @($plan.phase1) + @($plan.phase2)) {
        if ($process.ExecutablePath.StartsWith($row.absolutePath + '\', [StringComparison]::OrdinalIgnoreCase)) { throw "Running process in target: $($process.Name) pid=$($process.ProcessId)" }
    }
}

# Check the non-log content against the designated retained publish again.
foreach ($row in $before.phase2) {
    foreach ($file in $row.verifiedFiles.PSObject.Properties) {
        foreach ($prefix in @($row.path, $row.keep)) {
            $path = Join-Path $workspaceRoot ($prefix + '/' + $file.Name)
            if ((Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() -ne $file.Value.sha256) { throw "Duplicate publish no longer matches: $path" }
        }
    }
}
# Startup logs were already archived during the previous authorized attempt.
$archived = @(Get-Content -LiteralPath (Join-Path $evidenceRoot 'archived-startup-logs.json') -Raw -Encoding utf8 | ConvertFrom-Json)
if ($archived.Count -ne 4) { throw 'Expected four preserved startup logs' }
foreach ($log in $archived) {
    foreach ($path in @((Join-Path $workspaceRoot $log.source), $log.destination)) {
        if ((Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() -ne $log.sha256) { throw "Startup log preservation failed: $path" }
    }
}
$previousResult = Join-Path $evidenceRoot 'execution-result.json'
$blockedRecord = Join-Path $evidenceRoot 'blocked-execution-result.json'
if ((Test-Path -LiteralPath $previousResult) -and !(Test-Path -LiteralPath $blockedRecord)) { Copy-Item -LiteralPath $previousResult -Destination $blockedRecord }
Write-Output 'Fresh preflight PASS: 113 bounded targets; sources, archives, native DLL, processes and logs checked.'

foreach ($phase in @('phase1','phase2')) {
    $rows = if ($phase -eq 'phase1') { @($before.phase1) } else { @($before.phase2) }
    $count = 0
    foreach ($row in $rows) {
        try {
            Assert-Snapshot $row
            $absolute = Assert-Target $row.absolutePath
            Remove-Item -LiteralPath $absolute -Recurse -Force -ErrorAction Stop
            if (Test-Path -LiteralPath $absolute) { throw 'Target remains after deletion' }
            $results.Add([pscustomobject]@{ phase=$phase; path=$row.path; bytes=$row.current.bytes; fileCount=$row.current.fileCount; result='REMOVED'; error=$null })
        } catch {
            $results.Add([pscustomobject]@{ phase=$phase; path=$row.path; bytes=$row.current.bytes; fileCount=$row.current.fileCount; result='FAILED_OR_PARTIAL'; error=$_.Exception.Message })
        }
        $count++
        $results | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $evidenceRoot 'deletion-results.json') -Encoding utf8
        if (($count % 20) -eq 0) { Write-Output "$phase removed/checked $count/$($rows.Count)" }
    }
    Write-Output "$phase finished: $count targets"
}
$protectionResult = 'PASS'
$protectionError = $null
try { Assert-Protections } catch { $protectionResult='FAIL'; $protectionError=$_.Exception.Message }
$gates = [Collections.Generic.List[object]]::new()
foreach ($script in @('sync_release_sources.py','verify_ueformat_contract.py')) {
    $arguments = if ($script -eq 'sync_release_sources.py') { @('-B',(Join-Path $workspaceRoot ('scripts/' + $script)),'--preflight') } else { @('-B',(Join-Path $workspaceRoot ('scripts/' + $script))) }
    $output = (& python @arguments 2>&1 | Out-String)
    $exitCode = $LASTEXITCODE
    $output | Set-Content -LiteralPath (Join-Path $evidenceRoot ($script + '.log')) -Encoding utf8
    $gates.Add([pscustomobject]@{ script=$script; exitCode=$exitCode; result=if ($exitCode -eq 0) { 'PASS' } else { 'FAIL' } })
}
$failed = @($results | Where-Object { $_.result -ne 'REMOVED' })
$gateFailures = @($gates | Where-Object { $_.result -ne 'PASS' })
$success = $failed.Count -eq 0 -and $protectionResult -eq 'PASS' -and $gateFailures.Count -eq 0
$removedBytes = [long](($results | Where-Object { $_.result -eq 'REMOVED' } | Measure-Object -Property bytes -Sum).Sum)
$driveFreeAfter = [IO.DriveInfo]::new('D:\').AvailableFreeSpace
$summary = [pscustomobject]@{
    schemaVersion=1; date='2026-10-06'; status=if ($success) { 'PASS' } else { 'PARTIAL_OR_VALIDATION_FAILED' }
    authorization='User-requested cleanup with explicit execution escalation approval'
    removedTargets=@($results | Where-Object { $_.result -eq 'REMOVED' }).Count; failedTargets=$failed.Count
    removedFiles=[long](($results | Where-Object { $_.result -eq 'REMOVED' } | Measure-Object -Property fileCount -Sum).Sum)
    removedLogicalBytes=$removedBytes; removedLogicalGiB=[math]::Round($removedBytes/1GB,3)
    startupLogsArchived=4; protectedFilesValidation=$protectionResult; protectedFilesError=$protectionError
    repositoryAndWorktreeValidation=$protectionResult; validationGates=@($gates)
    driveAvailableBefore=$driveFreeBefore; driveAvailableAfter=$driveFreeAfter; observedAvailableSpaceIncrease=$driveFreeAfter-$driveFreeBefore
    aclChanged=$false; processesStopped=$false; blockedEarlierAttemptPreserved='blocked-execution-result.json'
}
$summary | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $previousResult -Encoding utf8
$summary | ConvertTo-Json -Depth 10 | Write-Output
$reportStatus = if ($success) { '完成并通过验证' } else { '部分完成或验证需要处理' }
$humanReport = @"
# 文件夹清理执行结果 — 2026-10-06

## 当前结果：$reportStatus

- 依据已核对清单执行用户授权的两个阶段，并通过目录外写入的执行审批。
- 已移除 $($summary.removedTargets) 个目标目录、$($summary.removedFiles) 个文件，逻辑大小 $($summary.removedLogicalBytes) bytes（约 $($summary.removedLogicalGiB) GiB）。
- 失败/部分清理目标：$($summary.failedTargets)。
- 4 份独有启动日志已归档并核对 SHA-256，位于 backups/2026-10-06-folder-cleanup-startup-logs。
- R68 包、源码/工作树状态、固定 native DLL、实验文件的保全检查：$protectionResult。
- readonly release preflight 与 V10 contract 检查结果见 execution-result.json 和两份 .log。
- 没有接管权限、修改 ACL 或停止进程；当前 R68 FModel.exe、publish-second、源码、Git 元数据和原生输入保留。

## 保留的其他内容

旧默认 R63 编译路径、独有历史版本、closed migration 镜像、未归档探针/补丁、恢复快照与拒绝读取区域不属于这次两个阶段的清理范围。

## 证据

- docs/current/evidence/2026-10-06/folder-cleanup-execution/before-snapshot.json：删除前快照。
- execution-plan.json：107 个可再生输出与 6 个重复发布目标。
- deletion-results.json：逐项删除结果。
- archived-startup-logs.json：日志原始字节校验和归档位置。
- execution-result.json：最终执行结果与保全、合同检查。
- blocked-execution-result.json：之前权限受阻记录，保留为历史。

磁盘可用空间变化的实测值记录在 execution-result.json；它可能受同期其他进程写入影响。以上清理大小以被移除的普通文件逻辑字节计。
"@
$humanReport | Set-Content -LiteralPath (Join-Path $workspaceRoot 'docs/current/FOLDER_CLEANUP_EXECUTION_2026-10-06.md') -Encoding utf8
if (!$success) { exit 1 }
