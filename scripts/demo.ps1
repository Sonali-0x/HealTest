param(
    [string]$ApiUrl = 'http://localhost:8000'
)

$ErrorActionPreference = 'Stop'
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
    -ContentType 'application/json' -Body $projectBody
$run = Invoke-RestMethod -Method Post `
    -Uri "$ApiUrl/projects/$($project.id)/runs"

Write-Host "Project created: $($project.id)"
Write-Host "Run submitted: $($run.id)"
Write-Host "Open the dashboard: http://localhost:3000/runs/$($run.id)"

$deadline = (Get-Date).AddMinutes(3)
do {
    Start-Sleep -Seconds 3
    $detail = Invoke-RestMethod -Uri "$ApiUrl/runs/$($run.id)"
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
