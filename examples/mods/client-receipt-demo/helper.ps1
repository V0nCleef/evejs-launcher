param(
    [Parameter(Mandatory = $true)][string]$RequestPath,
    [Parameter(Mandatory = $true)][string]$ResultPath
)
$ErrorActionPreference = 'Stop'
$protocol = 'evejs_launcher_mod_v1'
$request = $null
$utf8 = [System.Text.UTF8Encoding]::new($false)
try {
    $request = [System.IO.File]::ReadAllText($RequestPath) | ConvertFrom-Json
    if ($request.protocol -ne $protocol) { throw 'Unsupported request protocol.' }
    $client = [System.IO.Path]::GetFullPath([string]$request.runtime.clientRoot)
    if (-not [System.IO.Directory]::Exists($client)) { throw 'The captured client root is missing.' }
    $receiptPath = Join-Path $client '_local/mod-receipts/client-receipt-demo.json'
    foreach ($candidate in @((Join-Path $client '_local'), (Join-Path $client '_local/mod-receipts'), $receiptPath)) {
        $item = Get-Item -LiteralPath $candidate -Force -ErrorAction SilentlyContinue
        if ($null -ne $item -and (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -or $item.LinkType -eq 'HardLink')) {
            throw 'Demonstration receipt paths must not use links or junctions.'
        }
    }
    $receipt = $null
    if ([System.IO.File]::Exists($receiptPath)) {
        $receipt = [System.IO.File]::ReadAllText($receiptPath) | ConvertFrom-Json
        if ($receipt.modIdentity -ne $request.mod.identity -or $receipt.clientRoot -ne $client -or $receipt.schemaVersion -ne 1) {
            throw 'Existing demonstration receipt belongs to a different target or owner.'
        }
    }
    $desired = $null
    switch ([string]$request.action) {
        'install' { $desired = 'active' }
        'prepare_disable' { $desired = 'restored' }
        'prepare_remove' { $desired = 'restored' }
        'recover' {
            if ($null -eq $receipt) { throw 'No demonstration receipt exists to recover.' }
        }
        'verify' {
            if ($null -eq $receipt -or $receipt.state -ne 'active') { throw 'Install the demonstration enrollment first.' }
        }
        'prepare_profile' {
            if ($null -eq $request.profile) { throw 'A captured profile is required.' }
            if ($null -eq $receipt -or $receipt.state -ne 'active') { throw 'The demonstration enrollment is not active.' }
        }
        default { throw 'Unsupported helper action.' }
    }
    if ($null -ne $desired) {
        # This demonstration changes ONLY its own receipt. A real binary mod
        # must perform and verify its transaction before reporting this state.
        $receipt = [ordered]@{schemaVersion = 1; modIdentity = [string]$request.mod.identity; clientRoot = $client; state = $desired; demonstrationOnly = $true}
        $directory = [System.IO.Path]::GetDirectoryName($receiptPath)
        [System.IO.Directory]::CreateDirectory($directory) | Out-Null
        $temporary = Join-Path $directory ([System.Guid]::NewGuid().ToString('N') + '.tmp')
        try {
            [System.IO.File]::WriteAllText($temporary, ($receipt | ConvertTo-Json -Compress), $utf8)
            if ([System.IO.File]::Exists($receiptPath)) {
                [System.IO.File]::Replace($temporary, $receiptPath, [System.Management.Automation.Language.NullString]::Value)
            } else {
                [System.IO.File]::Move($temporary, $receiptPath)
            }
        } finally {
            if ([System.IO.File]::Exists($temporary)) { [System.IO.File]::Delete($temporary) }
        }
    }
    $result = [ordered]@{
        protocol = $protocol; requestId = [string]$request.requestId; success = $true; state = 'ready'
        message = 'Demonstration receipt verified; no client binaries were modified.'
        restartRequired = @(); contributions = @(); environment = @{}; arguments = @()
        receipt = @{base = 'client'; path = '_local/mod-receipts/client-receipt-demo.json'; state = [string]$receipt.state; schemaVersion = 1}
    }
} catch {
    $message = ($_.Exception.Message -replace '[\r\n]', ' ')
    $result = [ordered]@{
        protocol = $protocol; requestId = $(if ($null -ne $request) { [string]$request.requestId } else { 'invalid-request' })
        success = $false; state = 'failed'; message = $message.Substring(0, [Math]::Min(4096, $message.Length))
        restartRequired = @(); contributions = @(); environment = @{}; arguments = @()
    }
}
[System.IO.File]::WriteAllText($ResultPath, ($result | ConvertTo-Json -Depth 10 -Compress), $utf8)
if (-not $result.success) { exit 1 }
