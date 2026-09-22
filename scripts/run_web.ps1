$ErrorActionPreference = 'Stop'
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$BaseDir   = Split-Path -Parent $ScriptDir
Set-Location $BaseDir

$EnvFile = Join-Path $BaseDir 'api\.env'
if (Test-Path $EnvFile) {
    Get-Content $EnvFile | ForEach-Object {
        $line = $_.Trim()
        if ($line -and -not $line.StartsWith('#')) {
            $parts = $line -split '=', 2
            if ($parts.Count -eq 2) {
                [Environment]::SetEnvironmentVariable($parts[0].Trim(), $parts[1].Trim().Trim('"'), 'Process')
            }
        }
    }
}

$VenvPython = Join-Path $BaseDir 'venv\Scripts\python.exe'
& $VenvPython -m uvicorn api.app:app --host 0.0.0.0 --port 8000 --reload --reload-dir api
