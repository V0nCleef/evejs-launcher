#Requires -RunAsAdministrator
[CmdletBinding()]
param([string]$VmRoot = 'D:\EveJS-Linux-Test')
$ErrorActionPreference = 'Stop'
try {
    $root = (Resolve-Path -LiteralPath $VmRoot).Path.TrimEnd('\')
    if ($root -ne 'D:\EveJS-Linux-Test') { throw 'Unexpected project VM storage root.' }
    foreach ($path in @($root, "$root\project-vm.json", "$root\Ubuntu-24.04-EveJS.vhdx")) {
        $item = Get-Item -LiteralPath $path
        if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw "Linked VM storage is not supported: $path" }
    }
    $receipt = Get-Content -LiteralPath "$root\project-vm.json" -Raw | ConvertFrom-Json
    if ($receipt.purpose -ne 'evejs-launcher-linux-qa' -or $receipt.status -ne 'disk-ready') { throw 'The Ubuntu desktop disk is not prepared.' }
    $disk = "$root\Ubuntu-24.04-EveJS.vhdx"
    $name = 'EveJS-Linux-QA'
    $vm = Get-VM -Name $name -ErrorAction SilentlyContinue
    if ($null -ne $vm) {
        $drives = @(Get-VMHardDiskDrive -VM $vm)
        if ($drives.Count -ne 1 -or $drives[0].Path -ne $disk) { throw 'A different VM already uses the project name; nothing was changed.' }
    } else {
        if ((Get-FileHash -LiteralPath $disk -Algorithm SHA256).Hash -ne $receipt.diskSha256) { throw 'The prepared disk changed before VM registration.' }
        $switch = Get-VMSwitch -Name 'Default Switch' -ErrorAction Stop
        $vm = New-VM -Name $name -Generation 2 -MemoryStartupBytes 16GB -VHDPath $disk -Path $root -SwitchName $switch.Name
        Set-VMProcessor -VM $vm -Count 6
        Set-VM -VM $vm -AutomaticCheckpointsEnabled $false -AutomaticStartAction Nothing -AutomaticStopAction ShutDown
        Set-VMFirmware -VM $vm -EnableSecureBoot On -SecureBootTemplate MicrosoftUEFICertificateAuthority
        Set-VMVideo -VM $vm -HorizontalResolution 1440 -VerticalResolution 900 -ResolutionType Single
    }
    # Console access is limited to this VM; no global admin-group changes.
    Grant-VMConnectAccess -VMName $name -UserName "$env:USERDOMAIN\$env:USERNAME"
    if ($vm.State -eq 'Off') { Start-VM -VM $vm }
    [pscustomobject]@{ status='started'; name=$name; disk=$disk; ramGiB=16; vcpus=6; time=(Get-Date).ToString('o') } |
        ConvertTo-Json | Set-Content -LiteralPath "$root\registration-result.json" -Encoding utf8
} catch {
    [pscustomobject]@{ status='failed'; error=$_.Exception.Message; time=(Get-Date).ToString('o') } |
        ConvertTo-Json | Set-Content -LiteralPath "$VmRoot\registration-result.json" -Encoding utf8
    exit 1
}
