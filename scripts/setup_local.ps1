#!/usr/bin/env pwsh

$ErrorActionPreference = 'Stop'

function Write-Info([string]$Message) {
    Write-Host $Message -ForegroundColor Blue
}

function Write-Success([string]$Message) {
    Write-Host $Message -ForegroundColor Green
}

function Write-Warn([string]$Message) {
    Write-Host $Message -ForegroundColor Yellow
}

function Fail([string]$Message) {
    Write-Host "Error: $Message" -ForegroundColor Red
    exit 1
}

function Test-IsEnabled([string]$Value) {
    return $Value -eq 'true'
}

function New-HexSecret([int]$ByteCount) {
    $buffer = [byte[]]::new($ByteCount)
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $rng.GetBytes($buffer)
    } finally {
        $rng.Dispose()
    }
    return ($buffer | ForEach-Object { $_.ToString('x2') }) -join ''
}

function Read-SecretValue([string]$Prompt) {
    $readHostCommand = Get-Command Read-Host
    if ($readHostCommand.Parameters.ContainsKey('MaskInput')) {
        return Read-Host $Prompt -MaskInput
    }

    $secureValue = Read-Host $Prompt -AsSecureString
    $bstr = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureValue)
    try {
        return [System.Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
    } finally {
        [System.Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
    }
}

function Get-DefaultLanIPv4 {
    try {
        $routes = Get-NetRoute -AddressFamily IPv4 -DestinationPrefix '0.0.0.0/0' -ErrorAction Stop |
            Sort-Object -Property RouteMetric, InterfaceMetric

        foreach ($route in $routes) {
            $candidate = Get-NetIPAddress -AddressFamily IPv4 -InterfaceIndex $route.InterfaceIndex -ErrorAction Stop |
                Where-Object {
                    $_.IPAddress -ne '127.0.0.1' -and
                    -not $_.IPAddress.StartsWith('169.254.')
                } |
                Select-Object -First 1 -ExpandProperty IPAddress

            if ($candidate) {
                return $candidate
            }
        }
    } catch {
        # Fall back to generic interface enumeration below.
    }

    try {
        $interfaces = [System.Net.NetworkInformation.NetworkInterface]::GetAllNetworkInterfaces() |
            Where-Object {
                $_.OperationalStatus -eq [System.Net.NetworkInformation.OperationalStatus]::Up -and
                $_.NetworkInterfaceType -ne [System.Net.NetworkInformation.NetworkInterfaceType]::Loopback
            }

        foreach ($iface in $interfaces) {
            foreach ($unicast in $iface.GetIPProperties().UnicastAddresses) {
                if ($unicast.Address.AddressFamily -ne [System.Net.Sockets.AddressFamily]::InterNetwork) {
                    continue
                }

                $candidate = $unicast.Address.IPAddressToString
                if ($candidate -and $candidate -ne '127.0.0.1' -and -not $candidate.StartsWith('169.254.')) {
                    return $candidate
                }
            }
        }
    } catch {
        return $null
    }

    return $null
}

function Download-File([string]$Url, [string]$Destination) {
    $parent = Split-Path -Parent $Destination
    if ($parent) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }

    $params = @{
        Uri = $Url
        OutFile = $Destination
        ErrorAction = 'Stop'
    }

    $invokeWebRequest = Get-Command Invoke-WebRequest
    if ($invokeWebRequest.Parameters.ContainsKey('UseBasicParsing')) {
        $params.UseBasicParsing = $true
    }

    Invoke-WebRequest @params
}

function Download-BundleFileForRef([string]$Destination, [string]$RemotePath, [string]$Ref) {
    $rawBase = "https://raw.githubusercontent.com/dograh-hq/dograh/$Ref"
    $fallbackBase = 'https://raw.githubusercontent.com/dograh-hq/dograh/main'

    try {
        Download-File "$rawBase/$RemotePath" $Destination
    } catch {
        if ($Ref -eq 'main') {
            throw
        }

        Write-Warn "Warning: '$RemotePath' not found at '$Ref' - falling back to main"
        Download-File "$fallbackBase/$RemotePath" $Destination
    }
}

function Download-InitSupportBundle([string]$ProjectDir, [string]$Ref) {
    Download-BundleFileForRef (Join-Path $ProjectDir 'scripts/lib/setup_common.sh') 'scripts/lib/setup_common.sh' $Ref
    Download-BundleFileForRef (Join-Path $ProjectDir 'scripts/run_dograh_init.sh') 'scripts/run_dograh_init.sh' $Ref
    Download-BundleFileForRef (Join-Path $ProjectDir 'deploy/templates/nginx.remote.conf.template') 'deploy/templates/nginx.remote.conf.template' $Ref
    Download-BundleFileForRef (Join-Path $ProjectDir 'deploy/templates/turnserver.remote.conf.template') 'deploy/templates/turnserver.remote.conf.template' $Ref
}

function Assert-PathExists([string]$Path, [string]$Message) {
    if (-not (Test-Path $Path)) {
        Fail $Message
    }
}

Write-Info ''
Write-Info '╔══════════════════════════════════════════════════════════════╗'
Write-Info '║                    Dograh Local Setup                        ║'
Write-Info '║       Local docker deployment, optional TURN server          ║'
Write-Info '╚══════════════════════════════════════════════════════════════╝'
Write-Info ''

if ([string]::IsNullOrEmpty($env:ENABLE_COTURN)) {
    Write-Warn 'Enable coturn (TURN server) for WebRTC NAT traversal? [y/N]:'
    $enableCoturnInput = Read-Host '>'
    if ($enableCoturnInput -match '^[Yy]') {
        $EnableCoturn = 'true'
    } else {
        $EnableCoturn = 'false'
    }
} else {
    $EnableCoturn = $env:ENABLE_COTURN
}

$UseCoturn = Test-IsEnabled $EnableCoturn
$TurnHost = $env:TURN_HOST
$TurnSecret = $env:TURN_SECRET

if ($UseCoturn) {
    $defaultTurnHost = Get-DefaultLanIPv4
    if ([string]::IsNullOrEmpty($defaultTurnHost)) {
        $defaultTurnHost = '127.0.0.1'
    }

    if ([string]::IsNullOrEmpty($TurnHost)) {
        Write-Warn 'Enter the host browsers AND the API container will use to reach TURN'
        Write-Warn "(press Enter for $defaultTurnHost):"
        $TurnHost = Read-Host '>'
    }
    if ([string]::IsNullOrEmpty($TurnHost)) {
        $TurnHost = $defaultTurnHost
    }

    if ($TurnHost -notmatch '^[A-Za-z0-9.-]+$') {
        Fail 'TURN host must be an IP address or hostname'
    }

    if ([string]::IsNullOrEmpty($TurnSecret)) {
        Write-Warn 'Enter a shared secret for the TURN server (press Enter to generate a random one):'
        $TurnSecret = Read-SecretValue '>'
        Write-Host ''
    }

    if ([string]::IsNullOrEmpty($TurnSecret)) {
        $TurnSecret = New-HexSecret 32
        Write-Info 'Generated random TURN secret'
    }
}

$EnableTelemetry = if ([string]::IsNullOrEmpty($env:ENABLE_TELEMETRY)) { 'true' } else { $env:ENABLE_TELEMETRY }
$Registry = if ([string]::IsNullOrEmpty($env:REGISTRY)) { 'ghcr.io/dograh-hq' } else { $env:REGISTRY }

Write-Host ''
Write-Success 'Configuration:'
Write-Host "  Coturn:        $EnableCoturn" -ForegroundColor Blue
if ($UseCoturn) {
    Write-Host "  TURN Host:     $TurnHost" -ForegroundColor Blue
    Write-Host '  TURN Secret:   ********' -ForegroundColor Blue
}
Write-Host "  Telemetry:     $EnableTelemetry" -ForegroundColor Blue
Write-Host "  Registry:      $Registry" -ForegroundColor Blue
Write-Host ''

$TotalSteps = 2
$CurrentDir = (Get-Location).Path

if ($env:DOGRAH_SKIP_DOWNLOAD -ne '1') {
    if ($UseCoturn) {
        Write-Info "[1/$TotalSteps] Downloading docker-compose.yaml and TURN helper bundle..."
    } else {
        Write-Info "[1/$TotalSteps] Downloading docker-compose.yaml..."
    }

    Download-File 'https://raw.githubusercontent.com/dograh-hq/dograh/main/docker-compose.yaml' (Join-Path $CurrentDir 'docker-compose.yaml')
    if ($UseCoturn) {
        Download-InitSupportBundle $CurrentDir 'main'
    }

    Write-Success '✓ Deployment files downloaded'
} else {
    Write-Info "[1/$TotalSteps] Using docker-compose.yaml in current directory"
}

if ($UseCoturn) {
    Assert-PathExists 'scripts/run_dograh_init.sh' 'scripts/run_dograh_init.sh not found. Re-run setup_local.ps1 without DOGRAH_SKIP_DOWNLOAD=1, or use a full repo checkout.'
    Assert-PathExists 'scripts/lib/setup_common.sh' 'scripts/lib/setup_common.sh not found. Re-run setup_local.ps1 without DOGRAH_SKIP_DOWNLOAD=1, or use a full repo checkout.'
    Assert-PathExists 'deploy/templates/turnserver.remote.conf.template' 'deploy/templates/turnserver.remote.conf.template not found. Re-run setup_local.ps1 without DOGRAH_SKIP_DOWNLOAD=1, or use a full repo checkout.'
}

Write-Info "[2/$TotalSteps] Creating environment file..."
$ossJwtSecret = New-HexSecret 32

$envLines = @(
    '# Container registry for Dograh images'
    "REGISTRY=$Registry"
    ''
    '# JWT secret for OSS authentication'
    "OSS_JWT_SECRET=$ossJwtSecret"
    ''
    '# Telemetry (set to false to disable)'
    "ENABLE_TELEMETRY=$EnableTelemetry"
)

if ($UseCoturn) {
    $envLines += @(
        ''
        '# TURN Server Configuration (time-limited credentials via TURN REST API)'
        "TURN_HOST=$TurnHost"
        "TURN_SECRET=$TurnSecret"
    )
}

$envContent = ($envLines -join [Environment]::NewLine) + [Environment]::NewLine
[System.IO.File]::WriteAllText((Join-Path $CurrentDir '.env'), $envContent, [System.Text.UTF8Encoding]::new($false))
Write-Success '✓ .env file created'

Write-Host ''
Write-Success '╔══════════════════════════════════════════════════════════════╗'
Write-Success '║                    Setup Complete!                           ║'
Write-Success '╚══════════════════════════════════════════════════════════════╝'
Write-Host ''
Write-Host "Files created in $CurrentDir:" -ForegroundColor Blue
Write-Host '  - docker-compose.yaml'
Write-Host '  - .env'
if ($UseCoturn) {
    Write-Host '  - scripts/run_dograh_init.sh'
    Write-Host '  - scripts/lib/setup_common.sh'
    Write-Host '  - deploy/templates/'
}
Write-Host ''
if ($UseCoturn) {
    Write-Warn 'To start Dograh with TURN, run:'
    Write-Host ''
    Write-Host '  docker compose --profile local-turn up --pull always' -ForegroundColor Blue
} else {
    Write-Warn 'To start Dograh, run:'
    Write-Host ''
    Write-Host '  docker compose up --pull always' -ForegroundColor Blue
}
Write-Host ''
Write-Warn 'Your application will be available at:'
Write-Host ''
Write-Host '  http://localhost:3010' -ForegroundColor Blue
Write-Host ''
