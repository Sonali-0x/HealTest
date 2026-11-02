$ErrorActionPreference = 'Stop'

if (-not (Test-Path '.env')) {
    Copy-Item '.env.example' '.env'
    Write-Host 'Created .env from .env.example.'
}

docker compose up --build -d
if ($LASTEXITCODE -ne 0) {
    throw 'docker compose up failed.'
}

$deadline = (Get-Date).AddSeconds(90)
while ((Get-Date) -lt $deadline) {
    try {
        $health = Invoke-RestMethod -Uri 'http://localhost:8000/health' -TimeoutSec 3
        if ($health.status -eq 'ok') {
            Write-Host 'HealTest is ready: http://localhost:3000'
            exit 0
        }
    } catch {
        Start-Sleep -Seconds 3
    }
}

throw 'HealTest API did not become ready within 90 seconds. Inspect with: docker compose logs api'
