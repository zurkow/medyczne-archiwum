param(
    [Parameter(Mandatory)][string]$HostAddress,
    [int]$Port = 8443,
    [Parameter(Mandatory)][string]$CertFile,
    [Parameter(Mandatory)][string]$KeyFile
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $CertFile -PathType Leaf)) {
    throw "Nie znaleziono pliku certyfikatu TLS: $CertFile"
}
if (-not (Test-Path -LiteralPath $KeyFile -PathType Leaf)) {
    throw "Nie znaleziono pliku klucza prywatnego TLS: $KeyFile"
}
if ([string]::IsNullOrWhiteSpace($env:MEDICAL_ARCHIVE_SESSION_SECRET)) {
    throw "Ustaw zmienną środowiskową MEDICAL_ARCHIVE_SESSION_SECRET (co najmniej 32 losowe bajty)."
}

$env:MEDICAL_ARCHIVE_ENVIRONMENT = "production"
$env:MEDICAL_ARCHIVE_HOST = $HostAddress
$env:MEDICAL_ARCHIVE_PORT = [string]$Port
$env:MEDICAL_ARCHIVE_TLS_CERT = (Resolve-Path -LiteralPath $CertFile).Path
$env:MEDICAL_ARCHIVE_TLS_KEY = (Resolve-Path -LiteralPath $KeyFile).Path

python -m medical_archive
exit $LASTEXITCODE
