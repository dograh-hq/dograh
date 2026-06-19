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

if (-not (Test-Path 'docker-compose.yaml')) {
    Write-Error 'docker-compose.yaml not found. Download it first, then re-run this script.'
    exit 1
}

$existingSecret = Get-DotEnvValue -Path $EnvFile -Key 'OSS_JWT_SECRET'
if ([string]::IsNullOrEmpty($existingSecret)) {
    Set-DotEnvValue -Path $EnvFile -Key 'OSS_JWT_SECRET' -Value (New-HexSecret)
    Write-Host "Created OSS_JWT_SECRET in $EnvFile."
} else {
    Write-Host "OSS_JWT_SECRET is already set in $EnvFile."
}

Write-Host ''
Write-Host "Docker registry: $Registry"
Write-Host "Telemetry enabled: $EnableTelemetry"
Write-Host ''
Write-Host 'This will run:'
Write-Host "  `$env:REGISTRY = '$Registry'; `$env:ENABLE_TELEMETRY = '$EnableTelemetry'; docker compose up --pull always"
Write-Host ''

$answer = Read-Host 'Start Dograh now? [Y/n]'
if ($answer -match '^[Nn]') {
    Write-Host 'Dograh was not started.'
    exit 0
}

$env:REGISTRY = $Registry
$env:ENABLE_TELEMETRY = $EnableTelemetry
docker compose up --pull always
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}
