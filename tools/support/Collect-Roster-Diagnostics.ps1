$ErrorActionPreference = 'Stop'
$toolRoot = $PSScriptRoot
$executable = Join-Path $toolRoot 'Collector\EveJS-Roster-Collector.exe'
$reportRoot = Join-Path $toolRoot ('Reports\' + [DateTime]::Now.ToString('yyyyMMdd-HHmmss') + '-' + [Guid]::NewGuid().ToString('N').Substring(0,8))
$privateTargets = Join-Path $reportRoot 'targets.private.json'
$publicReport = Join-Path $reportRoot 'EveJS-Roster-Diagnostics.json'
function Invoke-Collector([string[]]$CollectorArguments) {
    # All arguments are generated here. Quote report paths with spaces.
    $quoted = foreach ($argument in $CollectorArguments) {
        if ($argument.Contains('"') -or $argument.EndsWith('\')) { throw 'Invalid collector argument' }
        '"' + $argument + '"'
    }
    $process = Start-Process -FilePath $executable -ArgumentList $quoted -WindowStyle Hidden -PassThru
    if (-not $process.WaitForExit(75000)) {
        $process.Kill()
        $process.WaitForExit()
        throw 'The collector timed out. No server start or stop was requested.'
    }
    if ($process.ExitCode -ne 0) { throw 'The collector could not create its report.' }
}
try {
    Write-Host 'EveJS roster diagnostics'
    Write-Host 'Leave your existing Docker Server running. This tool only reads data.'
    Write-Host 'It will not start/stop services, update mods, or change your Launcher settings.'
    if (-not (Test-Path -LiteralPath $executable -PathType Leaf)) {
        throw 'Extract the complete diagnostics ZIP first, including the Collector folder.'
    }
    New-Item -ItemType Directory -Path $reportRoot | Out-Null
    Invoke-Collector @('--list-roster-diagnostic-targets','--output',$privateTargets)
    $listing = Get-Content -LiteralPath $privateTargets -Raw | ConvertFrom-Json
    if ($listing.status -ne 'ready') { throw 'Docker could not be queried. Check Docker Desktop is running, then retry.' }
    $targets = @($listing.targets)
    if ($targets.Count -eq 0) { throw 'No running EveJS Docker Server was found. Start it normally in your Launcher, then retry.' }
    $selected = 0
    if ($targets.Count -gt 1) {
        Write-Host 'Choose the affected installation (these names stay local):'
        for ($index = 0; $index -lt $targets.Count; $index++) { Write-Host ('{0}) {1}' -f ($index + 1), $targets[$index].name) }
        $choice = Read-Host 'Number'
        $number = 0
        if (-not [int]::TryParse($choice,[ref]$number) -or $number -lt 1 -or $number -gt $targets.Count) { throw 'Selection cancelled.' }
        $selected = $number - 1
    }
    Invoke-Collector @('--collect-roster-diagnostics','--container-id',$targets[$selected].id,'--output',$publicReport)
    Write-Host ''
    Write-Host 'Done. Send ONLY this JSON file back:'
    Write-Host $publicReport
    Write-Host 'It contains stage/reason/type categories and counts, not character names, passwords, saves or raw exports.'
} catch {
    Write-Host $_.Exception.Message
} finally {
    # This chooser file is created by this run, under its unique report folder.
    if (Test-Path -LiteralPath $privateTargets -PathType Leaf) { Remove-Item -LiteralPath $privateTargets -Force }
}
