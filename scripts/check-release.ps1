$ErrorActionPreference = "Stop"

function Invoke-ReleaseStep {
    param(
        [Parameter(Mandatory)][string]$Name,
        [Parameter(Mandatory)][scriptblock]$Command
    )
    Write-Host "==> $Name"
    $global:LASTEXITCODE = 0
    try {
        & $Command
    }
    catch {
        throw "Release check failed at step '$Name': $($_.Exception.Message)"
    }
    if ($LASTEXITCODE -ne 0) {
        throw "Release check failed at step '$Name' (exit code $LASTEXITCODE)."
    }
}

$forbiddenFilePattern = '\.(db|sqlite|sqlite3|db-wal|db-shm|marchive|pem|key|crt|cer|p12|pfx)$|\.env$'

Push-Location (Split-Path -Parent $PSScriptRoot)
try {
    Invoke-ReleaseStep "ruff check" { python -m ruff check . }
    Invoke-ReleaseStep "ruff format --check" { python -m ruff format --check . }
    Invoke-ReleaseStep "pytest" { python -m pytest -q }

    $previousDatabaseUrl = $env:MEDICAL_ARCHIVE_DATABASE_URL
    $migrationDir = Join-Path ([IO.Path]::GetTempPath()) "medical-archive-release-$([guid]::NewGuid())"
    New-Item -ItemType Directory -Path $migrationDir | Out-Null
    try {
        $databasePath = (Join-Path $migrationDir "release-check.db") -replace "\\", "/"
        $env:MEDICAL_ARCHIVE_DATABASE_URL = "sqlite:///$databasePath"
        Invoke-ReleaseStep "alembic upgrade head" { python -m alembic upgrade head }
        Invoke-ReleaseStep "alembic downgrade base" { python -m alembic downgrade base }
        Invoke-ReleaseStep "alembic upgrade head (again)" { python -m alembic upgrade head }
    }
    finally {
        if ($null -eq $previousDatabaseUrl) {
            Remove-Item Env:MEDICAL_ARCHIVE_DATABASE_URL -ErrorAction SilentlyContinue
        }
        else {
            $env:MEDICAL_ARCHIVE_DATABASE_URL = $previousDatabaseUrl
        }
        Remove-Item -LiteralPath $migrationDir -Recurse -Force -ErrorAction SilentlyContinue
    }

    $preflightOutput = @()
    Invoke-ReleaseStep "preflight" {
        $script:preflightOutput = @(python -m medical_archive.preflight)
        $script:preflightOutput | ForEach-Object { Write-Host $_ }
    }
    if ($preflightOutput -contains "Preflight status: degraded") {
        Write-Warning "Preflight status is degraded: optional components are missing (see above)."
    }

    Invoke-ReleaseStep "git status (no data, backup, certificate, key or .env files)" {
        $statusLines = @(git status --porcelain --untracked-files=all)
        if ($LASTEXITCODE -ne 0) {
            return
        }
        $forbidden = @(
            $statusLines | Where-Object { $_.Trim().Trim('"') -match $forbiddenFilePattern }
        )
        if ($forbidden.Count -gt 0) {
            throw "forbidden files in git status: $($forbidden -join ', ')"
        }
    }
}
catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 1
}
finally {
    Pop-Location
}

Write-Host "Release checks passed"
exit 0
