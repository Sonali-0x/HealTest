param(
    [string]$ApiUrl = 'http://localhost:8000'
)

$ErrorActionPreference = 'Stop'
$apiToken = $env:HEALTEST_API_TOKEN
if (-not $apiToken) {
    $envPath = Join-Path (Split-Path -Parent $PSScriptRoot) '.env'
    if (Test-Path $envPath) {
        $tokenLine = Get-Content $envPath |
            Where-Object { $_ -match '^HEALTEST_API_TOKEN=' } |
            Select-Object -Last 1
        if ($tokenLine) {
            $apiToken = $tokenLine.Substring('HEALTEST_API_TOKEN='.Length).Trim()
        }
    }
}
if (-not $apiToken) {
    throw 'Set HEALTEST_API_TOKEN in .env before running the demo.'
}
$headers = @{ Authorization = "Bearer $apiToken" }

$health = Invoke-RestMethod -Uri "$ApiUrl/health"
if ($health.status -ne 'ok') {
    throw "HealTest API is not healthy at $ApiUrl."
}

$projectBody = @{
    name = "HealTest demo $(Get-Date -Format 'yyyyMMdd-HHmmss')"
    app_url = 'http://sample-apps:8901'
    test_path = '/sample_apps/todo/tests'
} | ConvertTo-Json
$project = Invoke-RestMethod -Method Post -Uri "$ApiUrl/projects" `
    -ContentType 'application/json' -Headers $headers -Body $projectBody
$run = Invoke-RestMethod -Method Post `
    -Uri "$ApiUrl/projects/$($project.id)/runs" -Headers $headers

Write-Host "Project created: $($project.id)"
Write-Host "Run submitted: $($run.id)"
Write-Host "Open the dashboard: http://localhost:3000/runs/$($run.id)"

$deadline = (Get-Date).AddMinutes(3)
do {
    Start-Sleep -Seconds 3
    $detail = Invoke-RestMethod -Uri "$ApiUrl/runs/$($run.id)" -Headers $headers
    $status = $detail.run.status
    Write-Host "Run status: $status"
} while ($status -in @('queued', 'running') -and (Get-Date) -lt $deadline)

if ($status -in @('queued', 'running')) {
    throw "Run $($run.id) is still active; inspect its status in the dashboard."
}

if ($status -eq 'error') {
    throw "Run $($run.id) ended in error: $($detail.run.error)"
}

Write-Host "Run completed with status '$status'. Review results and artifacts in the dashboard."
