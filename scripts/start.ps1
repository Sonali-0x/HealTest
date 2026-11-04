$ErrorActionPreference = 'Stop'

if (-not (Test-Path '.env')) {
    Copy-Item '.env.example' '.env'
    Write-Host 'Created .env from .env.example.'
}

$envLines = @(Get-Content '.env')
$redisLine = $envLines | Where-Object { $_ -match '^REDIS_PASSWORD=' } | Select-Object -First 1
if (-not $redisLine -or [string]::IsNullOrWhiteSpace($redisLine.Substring('REDIS_PASSWORD='.Length))) {
    $bytes = New-Object byte[] 32
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    $rng.GetBytes($bytes)
    $redisPassword = [Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_')
    $rng.Dispose()

    if ($redisLine) {
        $envLines = @($envLines | ForEach-Object {
            if ($_ -match '^REDIS_PASSWORD=') { "REDIS_PASSWORD=$redisPassword" } else { $_ }
        })
    } else {
        $envLines += "REDIS_PASSWORD=$redisPassword"
    }
    [System.IO.File]::WriteAllLines(
        (Join-Path (Get-Location) '.env'),
        [string[]]$envLines,
        (New-Object System.Text.UTF8Encoding($false))
    )
    Write-Host 'Generated a local Redis password in .env.'
}

$composeArgs = @('up', '--build', '-d')
$frontendListener = Get-NetTCPConnection -LocalPort 3000 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if ($frontendListener) {
    $composeArgs += @('api', 'redis', 'sample-apps')
    Write-Host 'Port 3000 is already in use; keeping the existing frontend and starting its backend only.'
} else {
    Write-Host 'Starting the dashboard, API, Redis, and sample apps. The sandbox worker is opt-in.'
}

docker compose @composeArgs
if ($LASTEXITCODE -ne 0) {
    throw 'docker compose up failed.'
}

$deadline = (Get-Date).AddSeconds(90)
while ((Get-Date) -lt $deadline) {
    try {
        $health = Invoke-RestMethod -Uri 'http://localhost:8000/health' -TimeoutSec 3
        if ($health.status -eq 'ok') {
            Write-Host 'HealTest API is ready: http://localhost:8000'
            Write-Host 'Dashboard: http://localhost:3000'
            Write-Host 'Run npm run dev from the frontend folder for a frontend development server.'
            exit 0
        }
    } catch {
        Start-Sleep -Seconds 3
    }
}

throw 'HealTest API did not become ready within 90 seconds. Inspect with: docker compose logs api'
