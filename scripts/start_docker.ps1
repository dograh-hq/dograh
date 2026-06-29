$ErrorActionPreference = 'Stop'

$EnvFile = '.env'
$Registry = if ([string]::IsNullOrEmpty($env:REGISTRY)) { 'ghcr.io/dograh-hq' } else { $env:REGISTRY }
$EnableTelemetry = if ([string]::IsNullOrEmpty($env:ENABLE_TELEMETRY)) { 'true' } else { $env:ENABLE_TELEMETRY }
$Utf8NoBom = [System.Text.UTF8Encoding]::new($false)

function New-HexSecret {
    $bytes = [byte[]]::new(32)
    [System.Security.Cryptography.RandomNumberGenerator]::Fill($bytes)
    return -join ($bytes | ForEach-Object { $_.ToString('x2') })
}

function New-MinioRootUser {
    return "dograh$((New-HexSecret).Substring(0, 12))"
}

function Get-DotEnvValue {
    param(
        [string]$Path,
        [string]$Key
    )

    if (-not (Test-Path $Path)) {
        return $null
    }

    $resolvedPath = (Resolve-Path $Path).Path
    foreach ($line in [System.IO.File]::ReadLines($resolvedPath)) {
        if ($line.StartsWith("$Key=")) {
            return $line.Substring($Key.Length + 1)
        }
    }

    return $null
}

function Set-DotEnvValue {
    param(
        [string]$Path,
        [string]$Key,
        [string]$Value
    )

    $lines = New-Object System.Collections.Generic.List[string]
    $updated = $false

    if (Test-Path $Path) {
        $resolvedPath = (Resolve-Path $Path).Path
        foreach ($line in [System.IO.File]::ReadLines($resolvedPath)) {
            if ($line.StartsWith("$Key=")) {
                $lines.Add("$Key=$Value")
                $updated = $true
            } else {
                $lines.Add($line)
            }
        }
    }

    if (-not $updated) {
        $lines.Add("$Key=$Value")
    }

    [System.IO.File]::WriteAllLines((Join-Path (Get-Location) $Path), $lines, $Utf8NoBom)
}

function Get-PostgresVolumeName {
    try {
        $configJson = docker compose config --format json 2>$null
        if ($LASTEXITCODE -eq 0 -and -not [string]::IsNullOrEmpty($configJson)) {
            $config = $configJson | ConvertFrom-Json
            $volumeName = $config.volumes.postgres_data.name
            if (-not [string]::IsNullOrEmpty($volumeName)) {
                return $volumeName
            }
        }
    } catch {
        # Fall back to Compose's default project-name convention below.
    }

    $projectName = if ([string]::IsNullOrEmpty($env:COMPOSE_PROJECT_NAME)) {
        (Split-Path -Leaf (Get-Location).Path).ToLowerInvariant() -replace '[^a-z0-9_-]', ''
    } else {
        $env:COMPOSE_PROJECT_NAME.ToLowerInvariant() -replace '[^a-z0-9_-]', ''
    }

    return "${projectName}_postgres_data"
}

function Test-DockerVolumeExists {
    param([string]$Name)

    docker volume inspect $Name *> $null
    return $LASTEXITCODE -eq 0
}

function Wait-PostgresReady {
    for ($attempt = 0; $attempt -lt 20; $attempt++) {
        docker compose exec -T postgres pg_isready -U postgres *> $null
        if ($LASTEXITCODE -eq 0) {
            return
        }
        Start-Sleep -Seconds 1
    }

    Write-Error 'Postgres did not become ready while syncing POSTGRES_PASSWORD.'
    exit 1
}

function Sync-PostgresPassword {
    param([string]$Password)

    if ([string]::IsNullOrEmpty($Password)) {
        return
    }

    $volumeName = Get-PostgresVolumeName
    if ([string]::IsNullOrEmpty($volumeName) -or -not (Test-DockerVolumeExists $volumeName)) {
        return
    }

    Write-Host "Existing Postgres volume detected; syncing postgres password from $EnvFile."
    $env:REGISTRY = $Registry
    $env:ENABLE_TELEMETRY = $EnableTelemetry
    docker compose up -d postgres
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }

    Wait-PostgresReady

    "ALTER USER postgres WITH PASSWORD :'dograh_password';" | docker compose exec -T postgres psql `
        -U postgres `
        -d postgres `
        -v 'ON_ERROR_STOP=1' `
        -v "dograh_password=$Password" > $null
    if ($LASTEXITCODE -ne 0) {
        Write-Error 'Failed to sync POSTGRES_PASSWORD with the existing Postgres volume.'
        exit $LASTEXITCODE
    }

    Write-Host 'Postgres password synced.'
}

if (-not (Test-Path 'docker-compose.yaml')) {
    Write-Error 'docker-compose.yaml not found. Download it first, then re-run this script.'
    exit 1
}

$envFileExisted = Test-Path $EnvFile

$existingSecret = Get-DotEnvValue -Path $EnvFile -Key 'OSS_JWT_SECRET'
if ([string]::IsNullOrEmpty($existingSecret)) {
    Set-DotEnvValue -Path $EnvFile -Key 'OSS_JWT_SECRET' -Value (New-HexSecret)
    Write-Host "Created OSS_JWT_SECRET in $EnvFile."
} else {
    Write-Host "OSS_JWT_SECRET is already set in $EnvFile."
}

$existingPostgresPassword = Get-DotEnvValue -Path $EnvFile -Key 'POSTGRES_PASSWORD'
if ([string]::IsNullOrEmpty($existingPostgresPassword)) {
    if (-not $envFileExisted) {
        Set-DotEnvValue -Path $EnvFile -Key 'POSTGRES_PASSWORD' -Value (New-HexSecret)
        Write-Host "Created POSTGRES_PASSWORD in $EnvFile."
    } else {
        Write-Host "POSTGRES_PASSWORD is not set in $EnvFile; keeping the docker-compose fallback for existing local data volumes."
    }
} else {
    Write-Host "POSTGRES_PASSWORD is already set in $EnvFile."
}

$existingRedisPassword = Get-DotEnvValue -Path $EnvFile -Key 'REDIS_PASSWORD'
if ([string]::IsNullOrEmpty($existingRedisPassword)) {
    Set-DotEnvValue -Path $EnvFile -Key 'REDIS_PASSWORD' -Value (New-HexSecret)
    Write-Host "Created REDIS_PASSWORD in $EnvFile."
} else {
    Write-Host "REDIS_PASSWORD is already set in $EnvFile."
}

$existingMinioRootUser = Get-DotEnvValue -Path $EnvFile -Key 'MINIO_ROOT_USER'
if ([string]::IsNullOrEmpty($existingMinioRootUser)) {
    $existingMinioAccessKey = Get-DotEnvValue -Path $EnvFile -Key 'MINIO_ACCESS_KEY'
    if ([string]::IsNullOrEmpty($existingMinioAccessKey)) {
        Set-DotEnvValue -Path $EnvFile -Key 'MINIO_ROOT_USER' -Value (New-MinioRootUser)
        Write-Host "Created MINIO_ROOT_USER in $EnvFile."
    } else {
        Set-DotEnvValue -Path $EnvFile -Key 'MINIO_ROOT_USER' -Value $existingMinioAccessKey
        Write-Host "Created MINIO_ROOT_USER in $EnvFile from existing MINIO_ACCESS_KEY."
    }
} else {
    Write-Host "MINIO_ROOT_USER is already set in $EnvFile."
}

$existingMinioRootPassword = Get-DotEnvValue -Path $EnvFile -Key 'MINIO_ROOT_PASSWORD'
if ([string]::IsNullOrEmpty($existingMinioRootPassword)) {
    $existingMinioSecretKey = Get-DotEnvValue -Path $EnvFile -Key 'MINIO_SECRET_KEY'
    if ([string]::IsNullOrEmpty($existingMinioSecretKey)) {
        Set-DotEnvValue -Path $EnvFile -Key 'MINIO_ROOT_PASSWORD' -Value (New-HexSecret)
        Write-Host "Created MINIO_ROOT_PASSWORD in $EnvFile."
    } else {
        Set-DotEnvValue -Path $EnvFile -Key 'MINIO_ROOT_PASSWORD' -Value $existingMinioSecretKey
        Write-Host "Created MINIO_ROOT_PASSWORD in $EnvFile from existing MINIO_SECRET_KEY."
    }
} else {
    Write-Host "MINIO_ROOT_PASSWORD is already set in $EnvFile."
}

Write-Host ''
Write-Host "Docker registry: $Registry"
Write-Host ''
Write-Host 'This will run:'
Write-Host "  `$env:REGISTRY = '$Registry'; `$env:ENABLE_TELEMETRY = '$EnableTelemetry'; docker compose --profile tunnel up --pull always"
Write-Host ''

$answer = Read-Host 'Start Dograh now? [Y/n]'
if ($answer -match '^[Nn]') {
    Write-Host 'Dograh was not started.'
    exit 0
}

$env:REGISTRY = $Registry
$env:ENABLE_TELEMETRY = $EnableTelemetry
Sync-PostgresPassword -Password (Get-DotEnvValue -Path $EnvFile -Key 'POSTGRES_PASSWORD')
docker compose --profile tunnel up --pull always
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}
