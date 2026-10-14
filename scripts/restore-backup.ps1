param([Parameter(Mandatory)][string]$BackupFile)

$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $BackupFile -PathType Leaf)) {
    throw "Nie znaleziono pliku kopii zapasowej: $BackupFile"
}

python -m medical_archive.backup.restore --backup-file $BackupFile
exit $LASTEXITCODE
