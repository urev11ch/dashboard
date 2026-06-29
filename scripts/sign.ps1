# Подписывает файл Authenticode-сертификатом, если заданы секреты.
# Используется в CI; локально не требуется.
param(
    [Parameter(Mandatory = $true)][string]$Path
)
$ErrorActionPreference = "Stop"

if (-not $env:WINDOWS_PFX_BASE64) {
    Write-Host "WINDOWS_PFX_BASE64 не задан — подпись пропущена для $Path"
    exit 0
}

$pfxPath = Join-Path $env:RUNNER_TEMP "codesign.pfx"
[IO.File]::WriteAllBytes($pfxPath, [Convert]::FromBase64String($env:WINDOWS_PFX_BASE64))

$signtool = Get-ChildItem "C:\Program Files (x86)\Windows Kits\10\bin\*\x64\signtool.exe" |
    Sort-Object FullName -Descending | Select-Object -First 1
if (-not $signtool) {
    throw "signtool.exe не найден (Windows SDK)."
}

& $signtool.FullName sign `
    /f $pfxPath `
    /p $env:WINDOWS_PFX_PASSWORD `
    /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 `
    $Path

Remove-Item $pfxPath -Force -ErrorAction SilentlyContinue
Write-Host "Подписано: $Path"
