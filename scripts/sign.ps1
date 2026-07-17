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
# $ErrorActionPreference = "Stop" на коды возврата нативных программ НЕ действует
# (для этого нужен $PSNativeCommandUseErrorActionPreference), поэтому код проверяем
# руками. Типовой транзиент — отвал сервера меток времени: без проверки скрипт
# напечатал бы «Подписано» и вернул 0, а в релиз уехал бы неподписанный файл.
$signExitCode = $LASTEXITCODE
Remove-Item $pfxPath -Force -ErrorAction SilentlyContinue
if ($signExitCode -ne 0) {
    throw "signtool sign завершился с кодом ${signExitCode}: $Path не подписан."
}

# Подпись проверяем отдельно: sign может отчитаться успехом, но /pa показывает,
# принимает ли её реальная политика Authenticode — то, что увидит SmartScreen.
& $signtool.FullName verify /pa $Path
if ($LASTEXITCODE -ne 0) {
    throw "signtool verify завершился с кодом ${LASTEXITCODE}: подпись $Path не прошла проверку."
}

Write-Host "Подписано: $Path"
